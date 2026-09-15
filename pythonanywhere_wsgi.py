"""PythonAnywhere WSGI Configuration Script.

Paste this into your PythonAnywhere Web Tab configuration file:
/var/www/YOUR_USERNAME_pythonanywhere_com_wsgi.py
"""

import sys
import os

# 1. Update project_folder path with your actual PythonAnywhere username
path = '/home/YOUR_USERNAME/POS_V2'
if path not in sys.path:
    sys.path.insert(0, path)

# 2. Load .env file
from dotenv import load_dotenv
env_file = os.path.join(path, '.env')
if os.path.exists(env_file):
    load_dotenv(env_file)

# 3. Import application object for WSGI server
from app import app as application
