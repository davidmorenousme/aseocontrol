from datetime import date

def empleado_trabaja_hoy(cur, empleado_id):
    hoy = date.today()

    # 1. Compensatorio activo hoy → no trabaja
    cur.execute("""
        SELECT id FROM novedades
        WHERE empleado_id = %s
          AND tipo = 'compensatorio'
          AND fecha_inicio <= %s
          AND (fecha_fin >= %s OR fecha_fin IS NULL)
        LIMIT 1
    """, (empleado_id, hoy, hoy))
    if cur.fetchone():
        return False

    # 2. Semana laboral configurada
    dia_semana = hoy.weekday()  # 0=Lunes … 6=Domingo
    cols = ['lunes','martes','miercoles','jueves','viernes','sabado','domingo']
    col  = cols[dia_semana]

    cur.execute(f"SELECT `{col}` FROM semana_laboral WHERE empleado_id = %s", (empleado_id,))
    row = cur.fetchone()

    if row is None:
        return dia_semana < 5  # sin config → Lun-Vie por defecto
    return bool(row[col])