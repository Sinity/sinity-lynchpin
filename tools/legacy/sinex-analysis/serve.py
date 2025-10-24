#!/usr/bin/env python3
"""
Simple live-reloading server with SQLite data API
Works with just Python standard library + watchdog (if available)
"""

import http.server
import socketserver
import json
import sqlite3
import os
import threading
import time
from datetime import datetime

PORT = 8080

class DataHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/metrics':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            # Try to read from SQLite if it exists
            if os.path.exists('metrics.db'):
                conn = sqlite3.connect('metrics.db')
                try:
                    # Get latest metric
                    latest = conn.execute('''
                        SELECT commit_hash, timestamp, metric_value 
                        FROM metrics 
                        WHERE metric_type='loc_total' 
                        ORDER BY timestamp DESC 
                        LIMIT 1
                    ''').fetchone()
                    
                    # Get history
                    history = conn.execute('''
                        SELECT timestamp, metric_value 
                        FROM metrics 
                        WHERE metric_type='loc_total' 
                        ORDER BY timestamp
                        LIMIT 50
                    ''').fetchall()
                    
                    data = {
                        'latest': {
                            'commit': latest[0] if latest else 'demo',
                            'timestamp': latest[1] if latest else time.time(),
                            'loc_total': latest[2] if latest else 162776
                        },
                        'history': [{'timestamp': h[0], 'value': h[1]} for h in history]
                    }
                except:
                    data = self.get_demo_data()
                finally:
                    conn.close()
            else:
                data = self.get_demo_data()
            
            self.wfile.write(json.dumps(data).encode())
        
        elif self.path == '/api/reload':
            # Simple endpoint to check if server is alive
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'alive')
        
        elif self.path == '/' or self.path.endswith('.html'):
            # Inject live reload script
            if self.path == '/':
                self.path = '/index.html'
                
            try:
                with open('web' + self.path, 'r') as f:
                    content = f.read()
                
                # Inject reload script before </body>
                reload_script = '''
<script>
// Simple live reload
(function() {
    let lastCheck = Date.now();
    setInterval(async () => {
        try {
            const response = await fetch('/api/reload');
            if (!response.ok) {
                location.reload();
            }
        } catch (e) {
            // Server restarted, reload page
            setTimeout(() => location.reload(), 1000);
        }
    }, 1000);
})();
</script>
</body>'''
                content = content.replace('</body>', reload_script)
                
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(content.encode())
            except:
                super().do_GET()
        else:
            # Serve static files from web/
            self.path = '/web' + self.path
            super().do_GET()
    
    def get_demo_data(self):
        """Demo data matching the sinex repository growth"""
        return {
            'latest': {
                'commit': 'demo123',
                'timestamp': time.time(),
                'loc_total': 162776
            },
            'history': [
                {'timestamp': 1717084800, 'value': 1338},    # May 30
                {'timestamp': 1717689600, 'value': 18281},   # Jun 5
                {'timestamp': 1718294400, 'value': 49773},   # Jun 13
                {'timestamp': 1718899200, 'value': 89018},   # Jun 20
                {'timestamp': 1719504000, 'value': 123897},  # Jun 27
                {'timestamp': 1720108800, 'value': 145505},  # Jul 4
                {'timestamp': 1720713600, 'value': 162776},  # Jul 11
            ]
        }

# Start server
os.chdir(os.path.dirname(os.path.abspath(__file__)))

print(f"🚀 Starting server at http://localhost:{PORT}")
print("📂 Serving from: web/")
print("💾 Database: metrics.db (will use demo data if not found)")
print("\nPress Ctrl+C to stop\n")

# Try to use ThreadingHTTPServer for better concurrency
try:
    with socketserver.ThreadingTCPServer(("", PORT), DataHandler) as httpd:
        httpd.serve_forever()
except:
    # Fallback to simple server
    with socketserver.TCPServer(("", PORT), DataHandler) as httpd:
        httpd.serve_forever()