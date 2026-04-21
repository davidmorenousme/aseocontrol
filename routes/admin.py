from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify, flash
from flask_mysqldb import MySQL
from datetime import date, datetime
from functools import wraps
import base64
 
admin = Blueprint('admin', __name__)
mysql = None
 
def init_mysql(mysql_instance):
    global mysql
    mysql = mysql_instance
 
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated
 
 
@admin.route('/admin')
@login_required
def dashboard():
    cur = mysql.connection.cursor()
    hoy = date.today()
 
    cur.execute("""
        SELECT COUNT(DISTINCT empleado_id) as total
        FROM registros
        WHERE DATE(timestamp) = %s AND tipo = 'entrada'
    """, (hoy,))
    presentes = cur.fetchone()['total']
 
    cur.execute("""
        SELECT COUNT(DISTINCT empleado_id) as total
        FROM registros
        WHERE DATE(timestamp) = %s AND tipo = 'salida_break'
        AND empleado_id NOT IN (
            SELECT empleado_id FROM registros
            WHERE DATE(timestamp) = %s AND tipo = 'regreso_break'
        )
    """, (hoy, hoy))
    en_break = cur.fetchone()['total']
 
    cur.execute("""
        SELECT COUNT(DISTINCT empleado_id) as total
        FROM registros
        WHERE DATE(timestamp) = %s AND tipo = 'salida_almuerzo'
        AND empleado_id NOT IN (
            SELECT empleado_id FROM registros
            WHERE DATE(timestamp) = %s AND tipo = 'regreso_almuerzo'
        )
    """, (hoy, hoy))
    en_almuerzo = cur.fetchone()['total']
 
    cur.execute("""
        SELECT COUNT(*) as total FROM novedades
        WHERE tipo = 'permiso'
        AND fecha_inicio <= %s
        AND (fecha_fin >= %s OR fecha_fin IS NULL)
    """, (hoy, hoy))
    permisos = cur.fetchone()['total']
 
    cur.execute("""
        SELECT COUNT(*) as total FROM novedades
        WHERE tipo = 'incapacidad'
        AND fecha_inicio <= %s
        AND (fecha_fin >= %s OR fecha_fin IS NULL)
    """, (hoy, hoy))
    incapacidades = cur.fetchone()['total']
 
    cur.execute("""
        SELECT
            c.id, c.nombre as conjunto,
            COUNT(DISTINCT e.id) as total_empleados,
            COUNT(DISTINCT CASE WHEN r.tipo = 'entrada'
                AND DATE(r.timestamp) = %s THEN r.empleado_id END) as presentes,
            COUNT(DISTINCT CASE WHEN r.tipo = 'salida_break'
                AND DATE(r.timestamp) = %s
                AND r.empleado_id NOT IN (
                    SELECT empleado_id FROM registros
                    WHERE tipo = 'regreso_break' AND DATE(timestamp) = %s
                ) THEN r.empleado_id END) as en_break,
            COUNT(DISTINCT CASE WHEN r.tipo = 'salida_almuerzo'
                AND DATE(r.timestamp) = %s
                AND r.empleado_id NOT IN (
                    SELECT empleado_id FROM registros
                    WHERE tipo = 'regreso_almuerzo' AND DATE(timestamp) = %s
                ) THEN r.empleado_id END) as en_almuerzo,
            COUNT(DISTINCT CASE WHEN n.tipo IN ('incapacidad','permiso','vacaciones')
                AND n.fecha_inicio <= %s
                AND (n.fecha_fin >= %s OR n.fecha_fin IS NULL)
                THEN e.id END) as con_novedad
        FROM conjuntos c
        LEFT JOIN empleados e ON e.conjunto_id = c.id AND e.activo = 1 AND e.tipo = 'fijo'
        LEFT JOIN registros r ON r.empleado_id = e.id
        LEFT JOIN novedades n ON n.empleado_id = e.id
        WHERE c.activo = 1
        GROUP BY c.id, c.nombre
        ORDER BY c.nombre
    """, (hoy, hoy, hoy, hoy, hoy, hoy, hoy))
    tarjetas = cur.fetchall()
 
    cur.execute("""
        SELECT
            e.id, e.nombre, e.documento,
            MAX(CASE WHEN r.tipo = 'entrada'          THEN TIME(r.timestamp) END) as entrada,
            MAX(CASE WHEN r.tipo = 'salida_break'     THEN TIME(r.timestamp) END) as sal_break,
            MAX(CASE WHEN r.tipo = 'regreso_break'    THEN TIME(r.timestamp) END) as reg_break,
            MAX(CASE WHEN r.tipo = 'salida_almuerzo'  THEN TIME(r.timestamp) END) as sal_almuerzo,
            MAX(CASE WHEN r.tipo = 'regreso_almuerzo' THEN TIME(r.timestamp) END) as reg_almuerzo,
            MAX(CASE WHEN r.tipo = 'salida'           THEN TIME(r.timestamp) END) as salida,
            (SELECT c.nombre FROM registros r2
             JOIN conjuntos c ON c.id = r2.conjunto_id
             WHERE r2.empleado_id = e.id
             AND r2.tipo = 'entrada'
             AND DATE(r2.timestamp) = %s
             LIMIT 1) as conjunto_hoy
        FROM empleados e
        LEFT JOIN registros r ON r.empleado_id = e.id AND DATE(r.timestamp) = %s
        WHERE e.tipo = 'supernumerario' AND e.activo = 1
        GROUP BY e.id, e.nombre, e.documento
        ORDER BY e.nombre
    """, (hoy, hoy))
    supernumerarios_raw = cur.fetchall()
 
    supernumerarios = []
    for s in supernumerarios_raw:
        if s['salida']:
            estado = 'Salió'
        elif s['sal_almuerzo'] and not s['reg_almuerzo']:
            estado = 'Almuerzo'
        elif s['sal_break'] and not s['reg_break']:
            estado = 'Break'
        elif s['entrada']:
            estado = 'Presente'
        else:
            estado = 'Sin registro'
 
        supernumerarios.append({
            'nombre':       s['nombre'],
            'documento':    s['documento'] or '—',
            'entrada':      str(s['entrada'])[:5] if s['entrada'] else '—',
            'estado':       estado,
            'conjunto_hoy': s['conjunto_hoy'] or '—',
        })
 
    cur.execute("""
        SELECT n.id, e.nombre, n.tipo, n.fecha_inicio, n.fecha_fin, n.descripcion
        FROM novedades n
        JOIN empleados e ON e.id = n.empleado_id
        WHERE n.fecha_fin >= %s OR n.fecha_fin IS NULL
        ORDER BY n.fecha_inicio DESC
        LIMIT 10
    """, (hoy,))
    novedades = cur.fetchall()
 
    cur.execute("""
        SELECT e.nombre, r.tipo, r.timestamp
        FROM registros r
        JOIN empleados e ON e.id = r.empleado_id
        WHERE DATE(r.timestamp) = %s
        ORDER BY r.timestamp DESC
        LIMIT 10
    """, (hoy,))
    actividad = cur.fetchall()
 
    cur.execute("SELECT id, nombre, documento FROM empleados WHERE activo = 1 ORDER BY nombre")
    empleados = cur.fetchall()
 
    cur.execute("SELECT id, nombre FROM conjuntos WHERE activo = 1 ORDER BY nombre")
    conjuntos = cur.fetchall()
 
    cur.close()
 
    return render_template('admin/dashboard.html',
        presentes=presentes,
        en_break=en_break,
        en_almuerzo=en_almuerzo,
        permisos=permisos,
        incapacidades=incapacidades,
        tarjetas=tarjetas,
        supernumerarios=supernumerarios,
        novedades=novedades,
        actividad=actividad,
        empleados=empleados,
        conjuntos=conjuntos,
        admin_nombre=session.get('admin_nombre'),
        fecha_hoy=datetime.today().strftime('%d/%m/%Y')
    )
 
 
# ══════════════════════════════════════════════════════
# DETALLE STAT CARDS
# ══════════════════════════════════════════════════════
 
@admin.route('/admin/stats/presentes')
@login_required
def stats_presentes():
    cur = mysql.connection.cursor()
    hoy = date.today()
    cur.execute("""
        SELECT e.nombre, e.documento, c.nombre as conjunto,
               TIME(r.timestamp) as hora_entrada
        FROM registros r
        JOIN empleados e ON e.id = r.empleado_id
        LEFT JOIN conjuntos c ON c.id = e.conjunto_id
        WHERE DATE(r.timestamp) = %s AND r.tipo = 'entrada'
        ORDER BY r.timestamp DESC
    """, (hoy,))
    rows = cur.fetchall()
    cur.close()
    return jsonify([{
        'nombre':       r['nombre'],
        'documento':    r['documento'] or '—',
        'conjunto':     r['conjunto'] or '—',
        'hora_entrada': str(r['hora_entrada'])[:5] if r['hora_entrada'] else '—',
    } for r in rows])
 
 
@admin.route('/admin/stats/break')
@login_required
def stats_break():
    cur = mysql.connection.cursor()
    hoy = date.today()
    cur.execute("""
        SELECT e.nombre, e.documento, c.nombre as conjunto,
               TIME(r.timestamp) as hora_salida
        FROM registros r
        JOIN empleados e ON e.id = r.empleado_id
        LEFT JOIN conjuntos c ON c.id = e.conjunto_id
        WHERE DATE(r.timestamp) = %s AND r.tipo = 'salida_break'
        AND r.empleado_id NOT IN (
            SELECT empleado_id FROM registros
            WHERE DATE(timestamp) = %s AND tipo = 'regreso_break'
        )
        ORDER BY r.timestamp DESC
    """, (hoy, hoy))
    rows = cur.fetchall()
    cur.close()
    return jsonify([{
        'nombre':      r['nombre'],
        'documento':   r['documento'] or '—',
        'conjunto':    r['conjunto'] or '—',
        'hora_salida': str(r['hora_salida'])[:5] if r['hora_salida'] else '—',
    } for r in rows])
 
 
@admin.route('/admin/stats/almuerzo')
@login_required
def stats_almuerzo():
    cur = mysql.connection.cursor()
    hoy = date.today()
    cur.execute("""
        SELECT e.nombre, e.documento, c.nombre as conjunto,
               MAX(r.timestamp) as hora_salida
        FROM registros r
        JOIN empleados e ON e.id = r.empleado_id
        LEFT JOIN conjuntos c ON c.id = e.conjunto_id
        WHERE DATE(r.timestamp) = %s AND r.tipo = 'salida_almuerzo'
        AND r.empleado_id NOT IN (
            SELECT empleado_id FROM registros
            WHERE DATE(timestamp) = %s AND tipo = 'regreso_almuerzo'
        )
        GROUP BY e.id, e.nombre, e.documento, c.nombre
        ORDER BY hora_salida DESC
    """, (hoy, hoy))
    rows = cur.fetchall()
    ahora = datetime.now()
    cur.close()
    resultado = []
    for r in rows:
        minutos = int((ahora - r['hora_salida']).total_seconds() / 60) if r['hora_salida'] else 0
        resultado.append({
            'nombre':      r['nombre'],
            'documento':   r['documento'] or '—',
            'conjunto':    r['conjunto'] or '—',
            'hora_salida': r['hora_salida'].strftime('%H:%M') if r['hora_salida'] else '—',
            'minutos':     minutos,
        })
    return jsonify(resultado)
 
 
@admin.route('/admin/stats/permisos')
@login_required
def stats_permisos():
    cur = mysql.connection.cursor()
    hoy = date.today()
    cur.execute("""
        SELECT e.nombre, e.documento, c.nombre as conjunto,
               n.fecha_inicio, n.fecha_fin, n.descripcion
        FROM novedades n
        JOIN empleados e ON e.id = n.empleado_id
        LEFT JOIN conjuntos c ON c.id = e.conjunto_id
        WHERE n.tipo = 'permiso'
        AND n.fecha_inicio <= %s
        AND (n.fecha_fin >= %s OR n.fecha_fin IS NULL)
        ORDER BY e.nombre
    """, (hoy, hoy))
    rows = cur.fetchall()
    cur.close()
    return jsonify([{
        'nombre':      r['nombre'],
        'documento':   r['documento'] or '—',
        'conjunto':    r['conjunto'] or '—',
        'fecha_inicio': r['fecha_inicio'].strftime('%d/%m/%Y') if r['fecha_inicio'] else '—',
        'fecha_fin':    r['fecha_fin'].strftime('%d/%m/%Y') if r['fecha_fin'] else 'Indefinido',
        'descripcion':  r['descripcion'] or '—',
    } for r in rows])
 
 
@admin.route('/admin/stats/incapacidades')
@login_required
def stats_incapacidades():
    cur = mysql.connection.cursor()
    hoy = date.today()
    cur.execute("""
        SELECT e.nombre, e.documento, c.nombre as conjunto,
               n.fecha_inicio, n.fecha_fin, n.descripcion
        FROM novedades n
        JOIN empleados e ON e.id = n.empleado_id
        LEFT JOIN conjuntos c ON c.id = e.conjunto_id
        WHERE n.tipo = 'incapacidad'
        AND n.fecha_inicio <= %s
        AND (n.fecha_fin >= %s OR n.fecha_fin IS NULL)
        ORDER BY e.nombre
    """, (hoy, hoy))
    rows = cur.fetchall()
    cur.close()
    return jsonify([{
        'nombre':      r['nombre'],
        'documento':   r['documento'] or '—',
        'conjunto':    r['conjunto'] or '—',
        'fecha_inicio': r['fecha_inicio'].strftime('%d/%m/%Y') if r['fecha_inicio'] else '—',
        'fecha_fin':    r['fecha_fin'].strftime('%d/%m/%Y') if r['fecha_fin'] else 'Indefinido',
        'descripcion':  r['descripcion'] or '—',
    } for r in rows])
 
 
# ══════════════════════════════════════════════════════
# DETALLE CONJUNTO
# ══════════════════════════════════════════════════════
@admin.route('/admin/conjunto/<int:conjunto_id>')
@login_required
def detalle_conjunto(conjunto_id):
    cur = mysql.connection.cursor()
    hoy = date.today()
 
    cur.execute("""
        SELECT
            e.nombre, e.documento,
            MAX(CASE WHEN r.tipo = 'entrada'          THEN TIME(r.timestamp) END) as entrada,
            MAX(CASE WHEN r.tipo = 'salida_break'     THEN TIME(r.timestamp) END) as sal_break,
            MAX(CASE WHEN r.tipo = 'regreso_break'    THEN TIME(r.timestamp) END) as reg_break,
            MAX(CASE WHEN r.tipo = 'salida_almuerzo'  THEN TIME(r.timestamp) END) as sal_almuerzo,
            MAX(CASE WHEN r.tipo = 'regreso_almuerzo' THEN TIME(r.timestamp) END) as reg_almuerzo,
            MAX(CASE WHEN r.tipo = 'salida'           THEN TIME(r.timestamp) END) as salida,
            n.tipo as novedad_tipo,
            (SELECT foto FROM registros
             WHERE empleado_id = e.id
             AND DATE(timestamp) = %s
             AND foto IS NOT NULL
             ORDER BY timestamp DESC LIMIT 1) as foto
        FROM empleados e
        LEFT JOIN registros r ON r.empleado_id = e.id AND DATE(r.timestamp) = %s
        LEFT JOIN novedades n ON n.empleado_id = e.id
            AND n.fecha_inicio <= %s
            AND (n.fecha_fin >= %s OR n.fecha_fin IS NULL)
        WHERE e.conjunto_id = %s AND e.activo = 1
        GROUP BY e.id, e.nombre, e.documento, n.tipo
        ORDER BY e.nombre
    """, (hoy, hoy, hoy, hoy, conjunto_id))
    empleados_raw = cur.fetchall()
 
    empleados = []
    for e in empleados_raw:
        if e['novedad_tipo'] in ('incapacidad', 'permiso', 'vacaciones'):
            estado = e['novedad_tipo'].capitalize()
        elif e['salida']:
            estado = 'Salió'
        elif e['sal_almuerzo'] and not e['reg_almuerzo']:
            estado = 'Almuerzo'
        elif e['sal_break'] and not e['reg_break']:
            estado = 'Break'
        elif e['entrada']:
            estado = 'Presente'
        else:
            estado = 'Sin registro'
 
        empleados.append({
            'nombre':    e['nombre'],
            'documento': e['documento'] or '—',
            'entrada':   str(e['entrada'])[:5] if e['entrada'] else '—',
            'estado':    estado,
            'foto': base64.b64encode(e['foto']).decode('utf-8') if e['foto'] else None,
        })
 
    cur.close()
    return jsonify(empleados)
 
 
# ══════════════════════════════════════════════════════
# NOVEDADES
# ══════════════════════════════════════════════════════
@admin.route('/admin/novedades', methods=['POST'])
@login_required
def crear_novedad():
    tipo        = request.form.get('tipo')
    empleado_id = request.form.get('empleado_id')
    cur = mysql.connection.cursor()
 
    if tipo == 'retiro':
        fecha_inicio = request.form.get('fecha_retiro')
        descripcion  = request.form.get('descripcion_r', '')
        cur.execute("""
            INSERT INTO novedades (empleado_id, tipo, fecha_inicio, descripcion)
            VALUES (%s, %s, %s, %s)
        """, (empleado_id, tipo, fecha_inicio, descripcion))
        cur.execute("UPDATE empleados SET activo = 0 WHERE id = %s", (empleado_id,))
 
    elif tipo == 'cambio_horario':
        fecha_inicio = request.form.get('fecha_inicio_h')
        fecha_fin    = request.form.get('fecha_fin_h') or None
        descripcion  = request.form.get('descripcion_h', '')
        hora_entrada = request.form.get('hora_entrada')
        hora_salida  = request.form.get('hora_salida')
        conjunto_id  = request.form.get('conjunto_id') or None
 
        cur.execute("""
            INSERT INTO novedades (empleado_id, tipo, fecha_inicio, fecha_fin, descripcion)
            VALUES (%s, %s, %s, %s, %s)
        """, (empleado_id, tipo, fecha_inicio, fecha_fin, descripcion))
 
        if hora_entrada and hora_salida:
            cur.execute("""
                INSERT INTO horarios (empleado_id, hora_entrada, hora_salida, vigente_desde)
                VALUES (%s, %s, %s, %s)
            """, (empleado_id, hora_entrada, hora_salida, fecha_inicio))
 
        if conjunto_id:
            cur.execute("UPDATE empleados SET conjunto_id = %s WHERE id = %s", (conjunto_id, empleado_id))
 
    else:
        fecha_inicio = request.form.get('fecha_inicio')
        fecha_fin    = request.form.get('fecha_fin') or None
        descripcion  = request.form.get('descripcion', '')
        cur.execute("""
            INSERT INTO novedades (empleado_id, tipo, fecha_inicio, fecha_fin, descripcion)
            VALUES (%s, %s, %s, %s, %s)
        """, (empleado_id, tipo, fecha_inicio, fecha_fin, descripcion))
 
    mysql.connection.commit()
    cur.close()
    return redirect(url_for('admin.dashboard'))
 
 
# ══════════════════════════════════════════════════════
# CREAR EMPLEADO
# ══════════════════════════════════════════════════════
@admin.route('/admin/empleados', methods=['POST'])
@login_required
def crear_empleado():
    nombre       = request.form.get('nombre', '').strip()
    documento    = request.form.get('documento', '').strip() or None
    cargo        = request.form.get('cargo', '').strip()
    telefono     = request.form.get('telefono', '').strip()
    tipo         = request.form.get('tipo', 'fijo')
    conjunto_id  = request.form.get('conjunto_id') or None
    hora_entrada = request.form.get('hora_entrada')
    hora_salida  = request.form.get('hora_salida')
 
    if not nombre:
        flash('El nombre es obligatorio')
        return redirect(url_for('admin.dashboard'))
 
    cur = mysql.connection.cursor()
 
    if documento:
        cur.execute("SELECT id FROM empleados WHERE documento = %s", (documento,))
        if cur.fetchone():
            cur.close()
            flash('Ya existe un empleado con ese documento')
            return redirect(url_for('admin.dashboard'))
 
    cur.execute("""
        INSERT INTO empleados (nombre, documento, cargo, telefono, tipo, conjunto_id, activo)
        VALUES (%s, %s, %s, %s, %s, %s, 1)
    """, (nombre, documento, cargo, telefono, tipo, conjunto_id))
 
    empleado_id = cur.lastrowid
 
    if hora_entrada and hora_salida:
        cur.execute("""
            INSERT INTO horarios (empleado_id, hora_entrada, hora_salida, vigente_desde)
            VALUES (%s, %s, %s, %s)
        """, (empleado_id, hora_entrada, hora_salida, date.today()))
 
    mysql.connection.commit()
    cur.close()
    return redirect(url_for('admin.dashboard'))
 
 
# ══════════════════════════════════════════════════════
# CREAR CONJUNTO
# ══════════════════════════════════════════════════════
@admin.route('/admin/conjuntos', methods=['POST'])
@login_required
def crear_conjunto():
    nombre    = request.form.get('nombre', '').strip()
    direccion = request.form.get('direccion', '').strip()
 
    if not nombre:
        flash('El nombre del conjunto es obligatorio')
        return redirect(url_for('admin.dashboard'))
 
    cur = mysql.connection.cursor()
    cur.execute("SELECT id FROM conjuntos WHERE nombre = %s", (nombre,))
    if cur.fetchone():
        cur.close()
        flash('Ya existe un conjunto con ese nombre')
        return redirect(url_for('admin.dashboard'))
 
    cur.execute("""
        INSERT INTO conjuntos (nombre, direccion, activo)
        VALUES (%s, %s, 1)
    """, (nombre, direccion))
 
    mysql.connection.commit()
    cur.close()
    return redirect(url_for('admin.dashboard'))