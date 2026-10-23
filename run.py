import os
from syncsphere import create_app

app = create_app()
socketio = app.extensions['socketio']
if __name__ == '__main__':
    socketio.run(app, host='127.0.0.1', port=int(os.environ.get('PORT', 5000)), allow_unsafe_werkzeug=True)
