import os
from dotenv import load_dotenv

load_dotenv()

MYSQL_HOST     = os.getenv('MYSQL_HOST', 'localhost')
MYSQL_USER     = os.getenv('MYSQL_USER', 'root')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', '')
MYSQL_DB       = os.getenv('MYSQL_DB', 'aseocontrol_db')
MYSQL_CURSORCLASS = 'DictCursor'

SECRET_KEY = os.getenv('SECRET_KEY', 'aseocontrol-2026')
DEBUG = True

MAIL_SERVER   = 'smtp.gmail.com'
MAIL_PORT     = 587
MAIL_USE_TLS  = True
MAIL_USERNAME = os.getenv('MAIL_USERNAME')
MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
MAIL_DESTINATARIO = os.getenv('MAIL_DESTINATARIO')