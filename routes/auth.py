from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from flask_mysqldb import MySQL
import bcrypt

auth = Blueprint('auth', __name__)
mysql = None

def init_mysql(mysql_instance):
    global mysql
    mysql = mysql_instance


@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        cur = mysql.connection.cursor()
        cur.execute("SELECT id, nombre, password_hash FROM usuarios_admin WHERE email = %s", (email,))
        admin = cur.fetchone()
        cur.close()

        if not admin:
            flash('Correo o contraseña incorrectos')
            return render_template('login.html')

        if bcrypt.checkpw(password.encode('utf-8'), admin['password_hash'].encode('utf-8')):
            session['admin_id']     = admin['id']
            session['admin_nombre'] = admin['nombre']
            return redirect(url_for('admin.dashboard'))
        else:
            flash('Correo o contraseña incorrectos')
            return render_template('login.html')

    return render_template('login.html')


@auth.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))