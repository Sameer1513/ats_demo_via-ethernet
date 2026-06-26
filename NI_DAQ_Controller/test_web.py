"""Quick test: run Flask server and verify it starts."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
os.chdir(os.path.dirname(__file__))

# Import our modules
from logger import initialize_logging, get_logger
initialize_logging(level='DEBUG')
log = get_logger('test')

# Create Flask app directly
from flask import Flask
app = Flask(__name__)

@app.route('/')
def index():
    return '<h1>NI DAQ Controller</h1><p>Server is running!</p><script>setInterval(()=>{fetch("/api/ping").then(r=>r.text()).then(console.log)},3000)</script>'

@app.route('/api/ping')
def ping():
    from datetime import datetime
    return f'pong at {datetime.now()}'

if __name__ == '__main__':
    print("=" * 50)
    print("  NI DAQ Controller - Web Server")
    print("=" * 50)
    print()
    print("  Open: http://localhost:5000")
    print()
    app.run(host='0.0.0.0', port=5000, debug=False)