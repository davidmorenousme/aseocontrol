from flask import Blueprint, jsonify, session, redirect, url_for, request
from datetime import datetime, date, timedelta
from functools import wraps
import smtplib
import pytz
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from routes.utils import empleado_trabaja_hoy

notificaciones_bp = Blueprint('notificaciones', __name__)
mysql       = None
mail_config = {}

def init_mysql(mysql_instance):
    global mysql
    mysql = mysql_instance

def init_mail(config):
    global mail_config
    mail_config = {
        'server':       getattr(config, 'MAIL_SERVER', None),
        'port':         getattr(config, 'MAIL_PORT', 587),
        'use_tls':      getattr(config, 'MAIL_USE_TLS', True),
        'username':     getattr(config, 'MAIL_USERNAME', None),
        'password':     getattr(config, 'MAIL_PASSWORD', None),
        'destinatario': getattr(config, 'MAIL_DESTINATARIO', None),
    }
    if not mail_config['username'] or not mail_config['password']:
        print("⚠️ ADVERTENCIA: Credenciales de correo no configuradas")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def enviar_correo(asunto, cuerpo_html):
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = asunto
        msg['From']    = mail_config['username']
        msg['To']      = mail_config['destinatario']
        msg.attach(MIMEText(cuerpo_html, 'html'))

        with smtplib.SMTP(mail_config['server'], mail_config['port'], timeout=10) as server:
            server.starttls()
            server.login(mail_config['username'], mail_config['password'])
            server.sendmail(mail_config['username'], mail_config['destinatario'], msg.as_string())

        print(f"✅ Correo enviado: {asunto}")
    except Exception as e:
        print(f"❌ Error enviando correo: {e}")


def ya_notificado_hoy(cur, tipo_notif, nombre_emp):
    cur.execute("""
        SELECT id FROM notificaciones
        WHERE tipo = %s
        AND mensaje LIKE %s
        AND DATE(creado_en) = %s
    """, (tipo_notif, f"%{nombre_emp}%", date.today()))
    return cur.fetchone() is not None


def guardar_notificacion(cur, tipo_notif, mensaje):
    cur.execute("""
        INSERT INTO notificaciones (tipo, mensaje)
        VALUES (%s, %s)
    """, (tipo_notif, mensaje))


def _plantilla_correo(titulo, subtitulo, encabezados, filas_html):
    ths = ''.join(f"<th style='padding:10px;text-align:left'>{h}</th>" for h in encabezados)
    return f"""
    <div style="font-family:sans-serif;max-width:620px;margin:auto">
      <div style="background:#2d7a4f;padding:20px;border-radius:8px 8px 0 0">
        <h2 style="color:white;margin:0">{titulo}</h2>
        <p style="color:rgba(255,255,255,0.8);margin:4px 0 0">{subtitulo}</p>
      </div>
      <div style="background:#f5f7f4;padding:20px;border-radius:0 0 8px 8px">
        <table style="width:100%;border-collapse:collapse;background:white;border-radius:8px;overflow:hidden">
          <thead><tr style="background:#e8f5ee">{ths}</tr></thead>
          <tbody>{filas_html}</tbody>
        </table>
        <p style="color:#6b8876;font-size:0.85rem;margin-top:16px">
          Este correo fue generado automáticamente por AseoControl.
        </p>
      </div>
    </div>
    """


def _timedelta_a_segundos(td):
    if hasattr(td, 'seconds'):
        return td.seconds
    return int(td.total_seconds())


# ══════════════════════════════════════════════════════════════
# VERIFICAR AUSENCIAS — +5 min después de hora de entrada
# ══════════════════════════════════════════════════════════════
def verificar_ausencias():
    cur = None
    try:
        cur = mysql.connection.cursor()
        hoy    = date.today()
        bogota = pytz.timezone('America/Bogota')
        ahora  = datetime.now(bogota).replace(tzinfo=None)

        cur.execute("""
            SELECT e.id, e.nombre, e.documento, h.hora_entrada,
                   c.nombre as conjunto
            FROM empleados e
            JOIN horarios h ON h.empleado_id = e.id
            LEFT JOIN conjuntos c ON c.id = e.conjunto_id
            WHERE e.activo = 1
            AND e.id NOT IN (
                SELECT empleado_id FROM registros
                WHERE DATE(timestamp) = %s AND tipo = 'entrada'
            )
            AND e.id NOT IN (
                SELECT empleado_id FROM novedades
                WHERE tipo IN ('incapacidad', 'vacaciones')
                AND fecha_inicio <= %s
                AND (fecha_fin >= %s OR fecha_fin IS NULL)
            )
            AND e.id NOT IN (
                SELECT empleado_id FROM novedades
                WHERE tipo = 'permiso'
                AND fecha_inicio <= %s
                AND (fecha_fin >= %s OR fecha_fin IS NULL)
            )
        """, (hoy, hoy, hoy, hoy, hoy))
        empleados_normales = cur.fetchall()

        cur.execute("""
            SELECT e.id, e.nombre, e.documento,
                   c.nombre as conjunto,
                   n.hora_entrada_permiso
            FROM empleados e
            JOIN novedades n ON n.empleado_id = e.id
            LEFT JOIN conjuntos c ON c.id = e.conjunto_id
            WHERE e.activo = 1
            AND n.tipo = 'permiso'
            AND n.fecha_inicio <= %s
            AND (n.fecha_fin >= %s OR n.fecha_fin IS NULL)
            AND n.hora_entrada_permiso IS NOT NULL
            AND e.id NOT IN (
                SELECT empleado_id FROM registros
                WHERE DATE(timestamp) = %s AND tipo = 'entrada'
            )
        """, (hoy, hoy, hoy))
        empleados_entrada_tarde = cur.fetchall()

        ausentes = []

        for emp in empleados_normales:
            if not emp['hora_entrada']:
                continue
            if not empleado_trabaja_hoy(cur, emp['id']):
                continue

            segundos    = _timedelta_a_segundos(emp['hora_entrada'])
            hora_limite = (
                datetime.combine(hoy, datetime.min.time())
                + timedelta(seconds=segundos)
                + timedelta(minutes=5)
            )

            if ahora >= hora_limite and not ya_notificado_hoy(cur, 'ausencia', emp['nombre']):
                ausentes.append({
                    'id':               emp['id'],
                    'nombre':           emp['nombre'],
                    'conjunto':         emp['conjunto'],
                    'hora_ref_fmt':     str(emp['hora_entrada'])[:5],
                    'es_entrada_tarde': False,
                })

        for emp in empleados_entrada_tarde:
            if not emp['hora_entrada_permiso']:
                continue
            if not empleado_trabaja_hoy(cur, emp['id']):
                continue

            segundos    = _timedelta_a_segundos(emp['hora_entrada_permiso'])
            hora_limite = (
                datetime.combine(hoy, datetime.min.time())
                + timedelta(seconds=segundos)
                + timedelta(minutes=5)
            )

            if ahora >= hora_limite and not ya_notificado_hoy(cur, 'ausencia', emp['nombre']):
                ausentes.append({
                    'id':               emp['id'],
                    'nombre':           emp['nombre'],
                    'conjunto':         emp['conjunto'],
                    'hora_ref_fmt':     str(emp['hora_entrada_permiso'])[:5],
                    'es_entrada_tarde': True,
                })

        if ausentes:
            for emp in ausentes:
                conjunto = emp['conjunto'] or 'Sin conjunto'
                if emp['es_entrada_tarde']:
                    mensaje = (
                        f"⚠️ {emp['nombre']} ({conjunto}) no ha marcado entrada "
                        f"(permiso entrada tarde hasta {emp['hora_ref_fmt']})"
                    )
                else:
                    mensaje = f"⚠️ {emp['nombre']} ({conjunto}) no ha marcado entrada (retraso +5 min)"
                guardar_notificacion(cur, 'ausencia', mensaje)

            mysql.connection.commit()

            lista_html = ''.join([
                f"<tr>"
                f"<td style='padding:8px;border-bottom:1px solid #eee'>{e['nombre']}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee'>{e['conjunto'] or '—'}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee'>"
                f"  {e['hora_ref_fmt']}"
                f"  {'<span style=\"font-size:0.75rem;color:#7c3aed;margin-left:4px\">(permiso)</span>' if e['es_entrada_tarde'] else ''}"
                f"</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee;color:#d97706;font-weight:600'>+5 min</td>"
                f"</tr>"
                for e in ausentes
            ])

            enviar_correo(
                f"⚠️ {len(ausentes)} empleado(s) sin marcar entrada — {hoy.strftime('%d/%m/%Y')}",
                _plantilla_correo(
                    titulo      = "⚠️ Empleados sin marcar entrada",
                    subtitulo   = f"{hoy.strftime('%d/%m/%Y')} — {ahora.strftime('%H:%M')}",
                    encabezados = ['Empleado', 'Conjunto', 'Hora entrada', 'Retraso'],
                    filas_html  = lista_html
                )
            )

        print(f"✅ verificar_ausencias OK — {len(ausentes)} ausente(s)")

    except Exception as e:
        print(f"❌ Error en verificar_ausencias: {e}")
    finally:
        if cur:
            cur.close()


# ══════════════════════════════════════════════════════════════
# VERIFICAR ALMUERZO — +65 min desde salida a almuerzo
# ══════════════════════════════════════════════════════════════
def verificar_almuerzo():
    cur = None
    try:
        cur = mysql.connection.cursor()
        hoy    = date.today()
        bogota = pytz.timezone('America/Bogota')
        ahora  = datetime.now(bogota).replace(tzinfo=None)

        cur.execute("""
            SELECT e.id, e.nombre, e.documento,
                   c.nombre as conjunto,
                   MAX(r.timestamp) as hora_salida_almuerzo
            FROM registros r
            JOIN empleados e ON e.id = r.empleado_id
            LEFT JOIN conjuntos c ON c.id = e.conjunto_id
            WHERE DATE(r.timestamp) = %s
            AND r.tipo = 'salida_almuerzo'
            AND e.id NOT IN (
                SELECT empleado_id FROM registros
                WHERE DATE(timestamp) = %s AND tipo = 'regreso_almuerzo'
            )
            GROUP BY e.id, e.nombre, e.documento, c.nombre
        """, (hoy, hoy))
        en_almuerzo = cur.fetchall()

        demorados = []
        for emp in en_almuerzo:
            if not emp['hora_salida_almuerzo']:
                continue

            hora_limite = emp['hora_salida_almuerzo'] + timedelta(minutes=65)

            if ahora >= hora_limite and not ya_notificado_hoy(cur, 'almuerzo_extendido', emp['nombre']):
                emp['hora_salida_almuerzo_fmt'] = emp['hora_salida_almuerzo'].strftime('%H:%M')
                demorados.append(emp)

        if demorados:
            for emp in demorados:
                conjunto = emp['conjunto'] or 'Sin conjunto'
                mensaje  = (
                    f"🍽️ {emp['nombre']} ({conjunto}) lleva más de 65 min en almuerzo "
                    f"(salió a las {emp['hora_salida_almuerzo_fmt']})"
                )
                guardar_notificacion(cur, 'almuerzo_extendido', mensaje)

            mysql.connection.commit()

            lista_html = ''.join([
                f"<tr>"
                f"<td style='padding:8px;border-bottom:1px solid #eee'>{e['nombre']}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee'>{e['conjunto'] or '—'}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee'>{e['hora_salida_almuerzo_fmt']}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee;color:#dc2626;font-weight:600'>+65 min</td>"
                f"</tr>"
                for e in demorados
            ])

            enviar_correo(
                f"🍽️ {len(demorados)} empleado(s) con almuerzo extendido — {hoy.strftime('%d/%m/%Y')}",
                _plantilla_correo(
                    titulo      = "🍽️ Empleados con almuerzo extendido",
                    subtitulo   = f"{hoy.strftime('%d/%m/%Y')} — {ahora.strftime('%H:%M')} · Límite: 60 min",
                    encabezados = ['Empleado', 'Conjunto', 'Salió a almorzar', 'Retraso'],
                    filas_html  = lista_html
                )
            )

        print(f"✅ verificar_almuerzo OK — {len(demorados)} demorado(s)")

    except Exception as e:
        print(f"❌ Error en verificar_almuerzo: {e}")
    finally:
        if cur:
            cur.close()


# ══════════════════════════════════════════════════════════════
# VERIFICAR BREAK — +20 min desde salida a break
# ══════════════════════════════════════════════════════════════
def verificar_break():
    cur = None
    try:
        cur = mysql.connection.cursor()
        hoy    = date.today()
        bogota = pytz.timezone('America/Bogota')
        ahora  = datetime.now(bogota).replace(tzinfo=None)

        cur.execute("""
            SELECT e.id, e.nombre, e.documento,
                   c.nombre as conjunto,
                   MAX(r.timestamp) as hora_salida_break
            FROM registros r
            JOIN empleados e ON e.id = r.empleado_id
            LEFT JOIN conjuntos c ON c.id = e.conjunto_id
            WHERE DATE(r.timestamp) = %s
            AND r.tipo = 'salida_break'
            AND e.id NOT IN (
                SELECT empleado_id FROM registros
                WHERE DATE(timestamp) = %s AND tipo = 'regreso_break'
            )
            GROUP BY e.id, e.nombre, e.documento, c.nombre
        """, (hoy, hoy))
        en_break = cur.fetchall()

        demorados = []
        for emp in en_break:
            if not emp['hora_salida_break']:
                continue

            hora_limite = emp['hora_salida_break'] + timedelta(minutes=20)

            if ahora >= hora_limite and not ya_notificado_hoy(cur, 'break_extendido', emp['nombre']):
                emp['hora_salida_break_fmt'] = emp['hora_salida_break'].strftime('%H:%M')
                demorados.append(emp)

        if demorados:
            for emp in demorados:
                conjunto = emp['conjunto'] or 'Sin conjunto'
                mensaje  = (
                    f"☕ {emp['nombre']} ({conjunto}) lleva más de 20 min en break "
                    f"(salió a las {emp['hora_salida_break_fmt']})"
                )
                guardar_notificacion(cur, 'break_extendido', mensaje)

            mysql.connection.commit()

            lista_html = ''.join([
                f"<tr>"
                f"<td style='padding:8px;border-bottom:1px solid #eee'>{e['nombre']}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee'>{e['conjunto'] or '—'}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee'>{e['hora_salida_break_fmt']}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee;color:#dc2626;font-weight:600'>+20 min</td>"
                f"</tr>"
                for e in demorados
            ])

            enviar_correo(
                f"☕ {len(demorados)} empleado(s) con break extendido — {hoy.strftime('%d/%m/%Y')}",
                _plantilla_correo(
                    titulo      = "☕ Empleados con break extendido",
                    subtitulo   = f"{hoy.strftime('%d/%m/%Y')} — {ahora.strftime('%H:%M')} · Límite: 15 min",
                    encabezados = ['Empleado', 'Conjunto', 'Salió a break', 'Retraso'],
                    filas_html  = lista_html
                )
            )

        print(f"✅ verificar_break OK — {len(demorados)} demorado(s)")

    except Exception as e:
        print(f"❌ Error en verificar_break: {e}")
    finally:
        if cur:
            cur.close()


# ══════════════════════════════════════════════════════════════
# RUTAS API NOTIFICACIONES
# ══════════════════════════════════════════════════════════════
@notificaciones_bp.route('/admin/notificaciones')
@login_required
def listar():
    cur = None
    try:
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT id, tipo, mensaje, leida, creado_en
            FROM notificaciones
            WHERE DATE(creado_en) = %s
            ORDER BY creado_en DESC
            LIMIT 30
        """, (date.today(),))
        notifs = cur.fetchall()

        cur.execute("""
            SELECT COUNT(*) as total FROM notificaciones
            WHERE leida = 0 AND DATE(creado_en) = %s
        """, (date.today(),))
        sin_leer = cur.fetchone()['total']

        return jsonify({
            'notificaciones': [
                {
                    'id':      n['id'],
                    'tipo':    n['tipo'],
                    'mensaje': n['mensaje'],
                    'leida':   n['leida'],
                    'hora':    n['creado_en'].strftime('%H:%M'),
                } for n in notifs
            ],
            'sin_leer': sin_leer
        })
    except Exception as e:
        print(f"❌ Error en listar notificaciones: {e}")
        return jsonify({'notificaciones': [], 'sin_leer': 0})
    finally:
        if cur:
            cur.close()


@notificaciones_bp.route('/admin/notificaciones/leer', methods=['POST'])
@login_required
def marcar_leidas():
    cur = None
    try:
        cur = mysql.connection.cursor()
        cur.execute("UPDATE notificaciones SET leida = 1 WHERE leida = 0")
        mysql.connection.commit()
        return jsonify({'ok': True})
    except Exception as e:
        print(f"❌ Error marcando leídas: {e}")
        return jsonify({'ok': False})
    finally:
        if cur:
            cur.close()


@notificaciones_bp.route('/admin/notificaciones/eliminar/<int:notif_id>', methods=['DELETE'])
@login_required
def eliminar_una(notif_id):
    cur = None
    try:
        cur = mysql.connection.cursor()
        cur.execute("DELETE FROM notificaciones WHERE id = %s", (notif_id,))
        mysql.connection.commit()
        return jsonify({'ok': True})
    except Exception as e:
        print(f"❌ Error eliminando notificación: {e}")
        return jsonify({'ok': False})
    finally:
        if cur:
            cur.close()


@notificaciones_bp.route('/admin/notificaciones/eliminar-todas', methods=['DELETE'])
@login_required
def eliminar_todas():
    cur = None
    try:
        cur = mysql.connection.cursor()
        cur.execute("DELETE FROM notificaciones WHERE DATE(creado_en) = %s", (date.today(),))
        mysql.connection.commit()
        return jsonify({'ok': True})
    except Exception as e:
        print(f"❌ Error eliminando notificaciones: {e}")
        return jsonify({'ok': False})
    finally:
        if cur:
            cur.close()
