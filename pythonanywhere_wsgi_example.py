# PythonAnywhere WSGI configuration for the Waste Collection Request System.
#
# How to use (see the Deployment Guide):
#   1. Web tab -> click the link beside "WSGI configuration file".
#   2. Delete everything in that file, paste this whole file in its place.
#   3. Change YOURNAME and YOUR-SECRET (explained below), Save, then press Reload.

import os
import sys

# 1. Where the project lives. Replace YOURNAME with your PythonAnywhere username.
PROJECT_HOME = "/home/YOURNAME/waste_collection_system"
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

# 2. Private settings. They are read by app.py and never shown on the website.
#    YOUR-SECRET: a long random text. Create one in a Bash console with:
#        python -c "import secrets; print(secrets.token_hex(32))"
os.environ["SECRET_KEY"] = "YOUR-SECRET"
os.environ["SECURE_COOKIES"] = "1"      # the live site uses HTTPS
os.environ["UTC_OFFSET_HOURS"] = "1"    # Nigeria time (UTC+1)

# 3. Start the Flask application. PythonAnywhere looks for the name "application".
from app import app as application  # noqa: E402
