#!/bin/sh
set -e
echo "=== KaGaMa Backend Starting ==="
echo "PORT=$PORT"
echo "Python: $(python --version 2>&1)"

# Test basic imports first
python -c "import flask; print(f'Flask {flask.__version__}')" 2>&1 || echo "IMPORT FAIL: flask"
python -c "import flask_sqlalchemy" 2>&1 || echo "IMPORT FAIL: flask_sqlalchemy"
python -c "import flask_login" 2>&1 || echo "IMPORT FAIL: flask_login"
python -c "import flask_limiter" 2>&1 || echo "IMPORT FAIL: flask_limiter"
python -c "import flask_mail" 2>&1 || echo "IMPORT FAIL: flask_mail"
python -c "import flask_wtf" 2>&1 || echo "IMPORT FAIL: flask_wtf"
python -c "import gunicorn" 2>&1 || echo "IMPORT FAIL: gunicorn"
python -c "import requests" 2>&1 || echo "IMPORT FAIL: requests"

echo "=== Imports done, testing flask_app import ==="
python -c "
import sys
try:
    import flask_app
    print('flask_app imported OK')
except Exception as e:
    print(f'flask_app IMPORT ERROR: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)
" 2>&1

echo "=== Starting gunicorn ==="
exec gunicorn flask_app:app --bind 0.0.0.0:${PORT:-8000} --timeout 120 --log-level debug --access-logfile - --error-logfile -
