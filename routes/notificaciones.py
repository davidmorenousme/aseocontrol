from flask import Blueprint, jsonify, session, redirect, url_for, request
from datetime import datetime, date, timedelta
from functools import wraps
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
 
notificaciones_bp = Blueprint('notificaciones', __name__)
mysql       = None
mail_config = {}
 
def init_mysql(mysql_instance):
    global mysql
    mysql = mysql_instance
 
def init_mail(config):
    global mail_config
    mail_config = {
        'server':       config.MAIL_SERVER,
        'port':         config.MAIL_PORT,
        'use_tls':      config.MAIL_USE_TLS,
        'username':     config.MAIL_USERNAME,
        'password':     config.MAIL_PASSWORD,
        'destinatario': config.MAIL_DESTINATARIO,
    }
 
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated
 
 
# ══════════════════════════════════════════════════════
# UTILIDAD: enviar correo
# ══════════════════════════════════════════════════════
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
 
 
# ══════════════════════════════════════════════════════
# UTILIDAD: plantilla HTML de correo
# ══════════════════════════════════════════════════════
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
 
 
# ══════════════════════════════════════════════════════
# 1. VERIFICAR AUSENCIAS
# ══════════════════════════════════════════════════════
def verificar_ausencias():
    cur = None
    try:
        cur = mysql.connection.cursor()
        hoy   = date.today()
        ahora = datetime.now()
 
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
                WHERE tipo IN ('permiso', 'incapacidad', 'vacaciones')
                AND fecha_inicio <= %s
                AND (fecha_fin >= %s OR fecha_fin IS NULL)
            )
        """, (hoy, hoy, hoy))
        empleados = cur.fetchall()
 
        ausentes = []
        for emp in empleados:
            if not emp['hora_entrada']:
                continue
 
            hora_entrada = emp['hora_entrada']
            segundos = hora_entrada.seconds if hasattr(hora_entrada, 'seconds') else int(hora_entrada.total_seconds())
 
            hora_inicio_ventana = (
                datetime.combine(hoy, datetime.min.time())
                + timedelta(seconds=segundos)
                + timedelta(minutes=15)
            )
            hora_fin_ventana = hora_inicio_ventana + timedelta(minutes=5)
 
            if hora_inicio_ventana <= ahora <= hora_fin_ventana and not ya_notificado_hoy(cur, 'ausencia', emp['nombre']):
                ausentes.append(emp)
 
        if ausentes:
            for emp in ausentes:
                conjunto = emp['conjunto'] or 'Sin conjunto'
                mensaje  = f"⚠️ {emp['nombre']} ({conjunto}) no ha marcado entrada"
                guardar_notificacion(cur, 'ausencia', mensaje)
 
            mysql.connection.commit()
 
            lista_html = ''.join([
                f"<tr>"
                f"<td style='padding:8px;border-bottom:1px solid #eee'>{e['nombre']}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee'>{e['conjunto'] or '—'}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee'>{str(e['hora_entrada'])[:5]}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee;color:#d97706;font-weight:600'>+15 min</td>"
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
 
        print(f"✅ verificar_ausencias OK — {len(ausentes)} ausente(s) en ventana")
 
    except Exception as e:
        print(f"❌ Error en verificar_ausencias: {e}")
    finally:
        if cur:
            cur.close()
 
 
# ══════════════════════════════════════════════════════
# 2. VERIFICAR ALMUERZO EXTENDIDO
# ══════════════════════════════════════════════════════
def verificar_almuerzo():
    cur = None
    try:
        cur = mysql.connection.cursor()
        hoy   = date.today()
        ahora = datetime.now()
 
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
 
            hora_inicio_ventana = emp['hora_salida_almuerzo'] + timedelta(minutes=75)
            hora_fin_ventana    = emp['hora_salida_almuerzo'] + timedelta(minutes=80)
 
            if hora_inicio_ventana <= ahora <= hora_fin_ventana and not ya_notificado_hoy(cur, 'almuerzo_extendido', emp['nombre']):
                minutos_fuera = int((ahora - emp['hora_salida_almuerzo']).total_seconds() / 60)
                emp['minutos_fuera']            = minutos_fuera
                emp['hora_salida_almuerzo_fmt'] = emp['hora_salida_almuerzo'].strftime('%H:%M')
                demorados.append(emp)
 
        if demorados:
            for emp in demorados:
                conjunto = emp['conjunto'] or 'Sin conjunto'
                mensaje  = (
                    f"🍽️ {emp['nombre']} ({conjunto}) lleva "
                    f"{emp['minutos_fuera']} min en almuerzo "
                    f"(salió a las {emp['hora_salida_almuerzo_fmt']})"
                )
                guardar_notificacion(cur, 'almuerzo_extendido', mensaje)
 
            mysql.connection.commit()
 
            lista_html = ''.join([
                f"<tr>"
                f"<td style='padding:8px;border-bottom:1px solid #eee'>{e['nombre']}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee'>{e['conjunto'] or '—'}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee'>{e['hora_salida_almuerzo_fmt']}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #eee;color:#dc2626;font-weight:600'>{e['minutos_fuera']} min</td>"
                f"</tr>"
                for e in demorados
            ])
 
            enviar_correo(
                f"🍽️ {len(demorados)} empleado(s) con almuerzo extendido — {hoy.strftime('%d/%m/%Y')}",
                _plantilla_correo(
                    titulo      = "🍽️ Empleados con almuerzo extendido",
                    subtitulo   = f"{hoy.strftime('%d/%m/%Y')} — {ahora.strftime('%H:%M')} · Límite: 1h 15min",
                    encabezados = ['Empleado', 'Conjunto', 'Salió a almorzar', 'Tiempo fuera'],
                    filas_html  = lista_html
                )
            )
 
        print(f"✅ verificar_almuerzo OK — {len(demorados)} demorado(s) en ventana")
 
    except Exception as e:
        print(f"❌ Error en verificar_almuerzo: {e}")
    finally:
        if cur:
            cur.close()
 
 
# ══════════════════════════════════════════════════════
# RUTAS DEL DASHBOARD
# ══════════════════════════════════════════════════════
 
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