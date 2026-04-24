import config

from flask import Flask, render_template, request, jsonify, session
from flask_mysqldb import MySQL
from datetime import date


from routes.auth import auth, init_mysql as auth_init
from routes.admin import admin, init_mysql as admin_init
from routes.reportes import reportes, init_mysql as reportes_init
from routes.notificaciones import (
    notificaciones_bp,
    init_mysql as notif_init,
    init_mail  as notif_mail,
    verificar_ausencias,
    verificar_almuerzo,
    verificar_break,
)
from routes.utils import empleado_trabaja_hoy

from apscheduler.schedulers.background import BackgroundScheduler
import atexit

app = Flask(__name__)

app.config.from_object(config)

mysql = MySQL(app)

auth_init(mysql)
admin_init(mysql)
reportes_init(mysql)
notif_init(mysql)
notif_mail(config)

app.register_blueprint(reportes)
app.register_blueprint(auth)
app.register_blueprint(admin)
app.register_blueprint(notificaciones_bp)






@app.route('/')
def index():
    return render_template('empleado.html')


@app.route('/consultar', methods=['POST'])
def consultar():
    data = request.get_json()
    documento = data.get('documento', '').strip()

    if not documento:
        return jsonify({'error': 'Documento requerido'}), 400

    cur = mysql.connection.cursor()

    cur.execute("""
        SELECT id, nombre, cargo, tipo
        FROM empleados
        WHERE documento = %s AND activo = 1
    """, (documento,))
    emp = cur.fetchone()

    if not emp:
        cur.close()
        return jsonify({'error': 'Empleado no encontrado'}), 404

    cur.execute("""
        SELECT hora_entrada, hora_salida
        FROM horarios
        WHERE empleado_id = %s
        ORDER BY vigente_desde DESC
        LIMIT 1
    """, (emp['id'],))
    horario = cur.fetchone()

    turno = None
    if horario:
        entrada = str(horario['hora_entrada'])[:5]
        salida  = str(horario['hora_salida'])[:5]
        turno = f"{entrada} – {salida}"

    # ── Validar si trabaja hoy según semana laboral ──
    if not empleado_trabaja_hoy(cur, emp['id']):
        cur.close()
        return jsonify({'error': 'dia_no_laboral', 'nombre': emp['nombre']}), 200

    cur.execute("""
        SELECT tipo, descripcion, fecha_fin
        FROM novedades
        WHERE empleado_id = %s
          AND fecha_inicio <= %s
          AND (fecha_fin >= %s OR fecha_fin IS NULL)
        ORDER BY fecha_inicio DESC
        LIMIT 1
    """, (emp['id'], date.today(), date.today()))
    novedad = cur.fetchone()

    cur.execute("""
        SELECT tipo FROM registros
        WHERE empleado_id = %s
        AND DATE(timestamp) = %s
        ORDER BY timestamp DESC
        LIMIT 1
    """, (emp['id'], date.today()))
    ultimo_registro = cur.fetchone()

    tiene_entrada    = False
    turno_finalizado = False

    if ultimo_registro:
        if ultimo_registro['tipo'] == 'salida':
            turno_finalizado = True
        else:
            tiene_entrada = True

    conjuntos = []
    if emp['tipo'] == 'supernumerario':
        cur.execute("SELECT id, nombre FROM conjuntos WHERE activo = 1 ORDER BY nombre")
        conjuntos = [{'id': c['id'], 'nombre': c['nombre']} for c in cur.fetchall()]

    cur.close()

    return jsonify({
        'id':               emp['id'],
        'nombre':           emp['nombre'],
        'cargo':            emp['cargo'],
        'turno':            turno,
        'tiene_entrada':    tiene_entrada,
        'turno_finalizado': turno_finalizado,
        'es_supernumerario': emp['tipo'] == 'supernumerario',
        'conjuntos': conjuntos if emp['tipo'] == 'supernumerario' else [],
        'novedad': {
            'tipo':        novedad['tipo'],
            'descripcion': novedad['descripcion'] or novedad['tipo'].capitalize(),
        } if novedad else None
    })


@app.route('/registrar', methods=['POST'])
def registrar():
    data        = request.get_json()
    empleado_id = data.get('empleado_id')
    tipo        = data.get('tipo')
    foto_b64    = data.get('foto')
    nombre_emp  = data.get('nombre')
    conjunto_id = data.get('conjunto_id') or None

    tipos_validos = ['entrada', 'salida_break', 'regreso_break',
                     'salida_almuerzo', 'regreso_almuerzo', 'salida']
    if not empleado_id or tipo not in tipos_validos:
        return jsonify({'error': 'Datos inválidos'}), 400

    foto_final = None

    if foto_b64:
        try:
            from PIL import Image, ImageDraw
            import base64, io
            from datetime import datetime

            header, encoded = foto_b64.split(',', 1)
            img_bytes = base64.b64decode(encoded)
            img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
            img.thumbnail((800, 800))

            ahora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
            texto = f"{nombre_emp}\n{ahora}"

            w, h = img.size
            overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
            draw_ov = ImageDraw.Draw(overlay)
            draw_ov.rectangle([0, h - 70, w, h], fill=(0, 0, 0, 140))
            img = Image.alpha_composite(img.convert('RGBA'), overlay).convert('RGB')

            draw = ImageDraw.Draw(img)
            draw.text((10, h - 65), texto, fill=(255, 255, 255))

            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=70)
            foto_final = buffer.getvalue()

        except Exception as e:
            print(f"Error procesando foto: {e}")

    cur = mysql.connection.cursor()
    cur.execute("""
        INSERT INTO registros (empleado_id, tipo, foto, foto_tipo, conjunto_id)
        VALUES (%s, %s, %s, %s, %s)
    """, (empleado_id, tipo, foto_final, 'jpeg' if foto_final else None, conjunto_id))
    mysql.connection.commit()
    cur.close()

    return jsonify({'ok': True})


@app.route('/api/dashboard-estado')
def dashboard_estado():
    if 'admin_id' not in session:
        return jsonify({'error': 'no auth'}), 401

    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT
            COALESCE(
                (SELECT MAX(id) FROM registros WHERE DATE(timestamp) = CURDATE()),
                0
            ) AS ultimo_registro,
            COALESCE(
                (SELECT COUNT(*) FROM notificaciones WHERE leida = 0),
                0
            ) AS no_leidas
    """)
    row = cur.fetchone()
    cur.close()

    return jsonify({'fingerprint': f"{row['ultimo_registro']}-{row['no_leidas']}"})


@app.route('/admin/semana-laboral/<int:empleado_id>')
def obtener_semana_laboral(empleado_id):
    if 'admin_id' not in session:
        return jsonify({'error': 'no auth'}), 401

    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT lunes, martes, miercoles, jueves, viernes, sabado, domingo
        FROM semana_laboral WHERE empleado_id = %s
    """, (empleado_id,))
    row = cur.fetchone()
    cur.close()

    if row:
        return jsonify(dict(row))
    return jsonify({
        'lunes': 1, 'martes': 1, 'miercoles': 1,
        'jueves': 1, 'viernes': 1, 'sabado': 0, 'domingo': 0
    })


@app.route('/admin/semana-laboral', methods=['POST'])
def guardar_semana_laboral():
    if 'admin_id' not in session:
        return jsonify({'error': 'no auth'}), 401

    data        = request.get_json()
    empleado_id = data.get('empleado_id')
    if not empleado_id:
        return jsonify({'error': 'empleado_id requerido'}), 400

    dias = {
        'lunes':     int(bool(data.get('lunes',     False))),
        'martes':    int(bool(data.get('martes',    False))),
        'miercoles': int(bool(data.get('miercoles', False))),
        'jueves':    int(bool(data.get('jueves',    False))),
        'viernes':   int(bool(data.get('viernes',   False))),
        'sabado':    int(bool(data.get('sabado',    False))),
        'domingo':   int(bool(data.get('domingo',   False))),
    }

    cur = mysql.connection.cursor()
    cur.execute("""
        INSERT INTO semana_laboral
            (empleado_id, lunes, martes, miercoles, jueves, viernes, sabado, domingo)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            lunes      = VALUES(lunes),
            martes     = VALUES(martes),
            miercoles  = VALUES(miercoles),
            jueves     = VALUES(jueves),
            viernes    = VALUES(viernes),
            sabado     = VALUES(sabado),
            domingo    = VALUES(domingo)
    """, (
        empleado_id,
        dias['lunes'], dias['martes'], dias['miercoles'],
        dias['jueves'], dias['viernes'], dias['sabado'], dias['domingo']
    ))
    mysql.connection.commit()
    cur.close()

    return jsonify({'ok': True})


@app.route('/test/ausencias')
def test_ausencias():
    verificar_ausencias()
    return 'Verificación de ausencias ejecutada'

@app.route('/test/almuerzo')
def test_almuerzo():
    verificar_almuerzo()
    return 'Verificación de almuerzo ejecutada'

@app.route('/test/break')
def test_break():
    verificar_break()
    return 'Verificación de break ejecutada'


def job_ausencias():
    with app.app_context():
        verificar_ausencias()

def job_almuerzo():
    with app.app_context():
        verificar_almuerzo()

def job_break():
    with app.app_context():
        verificar_break()


scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(job_ausencias, 'interval', minutes=6, id='job_ausencias')
scheduler.add_job(job_almuerzo,  'interval', minutes=6, id='job_almuerzo')
scheduler.add_job(job_break,     'interval', minutes=6, id='job_break')
scheduler.start()
atexit.register(lambda: scheduler.shutdown(wait=False))


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False, threaded=True)