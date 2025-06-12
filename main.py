# main.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_pymongo import PyMongo
from datetime import datetime, timedelta
import os # Keep this import as load_dotenv() uses os implicitly, and Config might too
from bson.objectid import ObjectId
from dateutil import parser
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from config import Config # Import your Config class

app = Flask(__name__)
app.config.from_object(Config) # Load configuration from Config class

# MongoDB Configuration
mongo = PyMongo(app)
db = mongo.db # Directly assign the database object after successful initialization

# Socket.IO initialization
socketio = SocketIO(app)

@app.route('/')
def home():
    if 'phone' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/login', methods=['POST'])
def login():
    if db is None:
        return "Database not connected. Please check server logs.", 500
    phone = request.form.get('phone')
    user = db.users.find_one({"phone": phone})
    if user:
        session['phone'] = phone
        session['username'] = user.get('username', phone)
        session['show_intro'] = True
        session.pop('urgent_notification_sent_http', None)
        session.pop('urgent_notification_sent_socket_for_this_request', None)
        return redirect(url_for('dashboard'))
    return "User not found. Please register first."

@app.route('/register', methods=['POST'])
def register():
    if db is None:
        return "Database not connected. Please check server logs.", 500
    phone = request.form.get('phone')
    username = request.form.get('username')
    if db.users.find_one({"phone": phone}):
        return "Phone already registered!"
    
    db.users.insert_one({
        "phone": phone,
        "username": username,
        "created_at": datetime.now(),
        "theme_preference": "light",
        "notification_token": ""
    })
    session['phone'] = phone
    session['username'] = username
    session['show_intro'] = True
    session.pop('urgent_notification_sent_http', None)
    session.pop('urgent_notification_sent_socket_for_this_request', None)
    return redirect(url_for('dashboard'))

# New Intro Route - always renders intro.html when accessed
@app.route('/intro')
def intro():
    return render_template('intro.html')

@app.route('/dashboard')
def dashboard():
    if 'phone' not in session:
        return redirect(url_for('home'))

    # Check if intro should be shown and redirect *before* processing dashboard content
    if session.get('show_intro'):
        session.pop('show_intro', None) # Clear the flag immediately
        return redirect(url_for('intro'))
    
    # --- Check for DB connection BEFORE making DB calls ---
    if db is None:
        return "Database not connected. Please check server logs.", 500

    user_phone = session['phone']
    username = session.get('username', user_phone)

    # --- Automated Time-Out Task Handling ---
    current_time = datetime.now()
    timed_out_tasks_cursor = db.todoLists.find({
        "phone": user_phone,
        "status": "ongoing",
        "deadline": {"$lt": current_time.isoformat(timespec='minutes')}
    })

    timed_out_tasks_list = list(timed_out_tasks_cursor)

    for task in timed_out_tasks_list:
        db.taskHistory.insert_one({
            "phone": user_phone,
            "title": task['title'],
            "description": task['description'],
            "priority": task['priority'],
            "deadline": task['deadline'],
            "action": "TimeOut",
            "action_at": datetime.now()
        })
        db.todoLists.delete_one({"_id": task['_id']})
    # --- End Time-Out Task Handling ---

    # Filtering parameters
    priority_filter = request.args.get('priority_filter')
    status_filter = request.args.get('status_filter')
    search_query = request.args.get('search_query')

    query = {"phone": user_phone}

    if priority_filter and priority_filter != 'all':
        query['priority'] = priority_filter
    
    if status_filter and status_filter != 'all':
        query['status'] = status_filter

    if search_query:
        query['$or'] = [
            {"title": {"$regex": search_query, "$options": "i"}},
            {"description": {"$regex": search_query, "$options": "i"}}
        ]
    
    tasks = list(db.todoLists.find(query).sort([("order", 1), ("created_at", 1)]))
    
    # Process tasks to calculate days left and countdown
    for task in tasks:
        if 'deadline' in task:
            try:
                deadline = parser.parse(task['deadline'])
                now = datetime.now()
                delta = deadline - now
                
                if delta.total_seconds() > 0:
                    total_seconds = int(delta.total_seconds())
                    task['countdown_target'] = deadline.isoformat()
                    task['countdown_initial'] = {
                        'days': total_seconds // (24 * 3600),
                        'hours': (total_seconds % (24 * 3600)) // 3600,
                        'minutes': (total_seconds % 3600) // 60,
                        'seconds': total_seconds % 60
                    }
                else:
                    task['countdown_initial'] = None
                    task['countdown_target'] = None
            except Exception as e:
                print(f"Error parsing deadline for task {task.get('_id')}: {e}")
                task['deadline_status'] = 'invalid'
                task['days_left'] = 'Invalid date'
                task['countdown_initial'] = None
                task['countdown_target'] = None
    
    return render_template('dashboard.html', 
                            username=username,
                            tasks=tasks,
                            priority_filter=priority_filter,
                            status_filter=status_filter,
                            search_query=search_query)

# --- Socket.IO Event Handler for Urgent Tasks ---
@socketio.on('request_urgent_tasks')
def handle_request_urgent_tasks():
    if 'phone' not in session:
        return
    
    if db is None: # Added DB check
        print("WARNING: Socket.IO handler tried to access DB but connection is None.")
        return

    user_phone = session['phone']
    current_time = datetime.now()
    
    if session.get('urgent_notification_sent_socket_for_this_request'):
        return

    urgent_tasks = []
    tasks_to_check = db.todoLists.find({
        "phone": user_phone,
        "status": "ongoing",
        "deadline": {"$gt": current_time.isoformat(timespec='minutes')}
    })

    for task in tasks_to_check:
        if 'deadline' in task:
            try:
                deadline = parser.parse(task['deadline'])
                now = datetime.now()
                delta = deadline - now
                
                if 0 < delta.total_seconds() <= 24 * 3600:
                    urgent_tasks.append({
                        'title': task['title'],
                        'deadline': task['deadline']
                    })
            except Exception as e:
                print(f"Error parsing deadline for task {task.get('_id')} in Socket.IO handler: {e}")
    
    if urgent_tasks:
        emit('urgent_tasks_notification', urgent_tasks, room=request.sid)
        session['urgent_notification_sent_socket_for_this_request'] = True

@socketio.on('disconnect')
def test_disconnect():
    print(f'Client disconnected: {request.sid}')

@app.route('/add_task', methods=['POST'])
def add_task():
    if 'phone' not in session:
        return redirect(url_for('home'))
    
    if db is None: # Added DB check
        return "Database not connected. Please check server logs.", 500

    user_phone = session['phone']
    task_title = request.form.get('task_title')
    task_description = request.form.get('task_description')
    task_priority = request.form.get('task_priority')
    task_deadline = request.form.get('task_deadline')

    if task_title and task_description and task_priority and task_deadline:
        next_order = db.todoLists.count_documents({"phone": user_phone})
        
        db.todoLists.insert_one({
            "phone": user_phone,
            "title": task_title,
            "description": task_description,
            "status": "ongoing",
            "priority": task_priority,
            "deadline": task_deadline,
            "created_at": datetime.now(),
            "order": next_order
        })
    return redirect(url_for('dashboard'))

@app.route('/update_task/<task_id>', methods=['POST'])
def update_task(task_id):
    if 'phone' not in session:
        return redirect(url_for('home'))
    
    if db is None: # Added DB check
        return "Database not connected. Please check server logs.", 500

    user_phone = session['phone']
    task = db.todoLists.find_one({"_id": ObjectId(task_id), "phone": user_phone})
    if task:
        new_title = request.form.get('new_title', task['title'])
        new_description = request.form.get('new_description', task['description'])
        new_status = request.form.get('new_status', task['status'])
        new_priority = request.form.get('new_priority', task['priority'])
        new_deadline = request.form.get('new_deadline', task['deadline'])

        db.todoLists.update_one(
            {"_id": ObjectId(task_id)},
            {"$set": {
                "title": new_title,
                "description": new_description,
                "status": new_status,
                "priority": new_priority,
                "deadline": new_deadline
            }}
        )
    return redirect(url_for('dashboard'))

@app.route('/complete_task/<task_id>', methods=['POST'])
def complete_task(task_id):
    if 'phone' not in session:
        return redirect(url_for('home'))
    
    if db is None: # Added DB check
        return "Database not connected. Please check server logs.", 500

    user_phone = session['phone']
    task = db.todoLists.find_one_and_delete({"_id": ObjectId(task_id), "phone": user_phone})
    
    if task:
        db.taskHistory.insert_one({
            "phone": user_phone,
            "title": task['title'],
            "description": task['description'],
            "priority": task['priority'],
            "deadline": task['deadline'],
            "action": "completed",
            "action_at": datetime.now()
        })
    return redirect(url_for('dashboard'))

@app.route('/delete_task/<task_id>', methods=['POST'])
def delete_task(task_id):
    if 'phone' not in session:
        return redirect(url_for('home'))
    
    if db is None: # Added DB check
        return "Database not connected. Please check server logs.", 500

    user_phone = session['phone']
    task = db.todoLists.find_one_and_delete({"_id": ObjectId(task_id), "phone": user_phone})
    
    if task:
        db.taskHistory.insert_one({
            "phone": user_phone,
            "title": task['title'],
            "description": task['description'],
            "priority": task['priority'],
            "deadline": task['deadline'],
            "action": "deleted",
            "action_at": datetime.now()
        })
    return redirect(url_for('dashboard'))

@app.route('/update_task_order', methods=['POST'])
def update_task_order():
    if 'phone' not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    if db is None: # Added DB check
        return jsonify({"success": False, "message": "Database not connected"}), 500

    user_phone = session['phone']
    try:
        ordered_task_ids = request.json.get('order')
        if not ordered_task_ids or not isinstance(ordered_task_ids, list):
            return jsonify({"success": False, "message": "Invalid order data"}), 400

        for index, task_id_str in enumerate(ordered_task_ids):
            db.todoLists.update_one(
                {"_id": ObjectId(task_id_str), "phone": user_phone},
                {"$set": {"order": index}}
            )
        return jsonify({"success": True, "message": "Task order updated successfully"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/history')
def history():
    if 'phone' not in session:
        return redirect(url_for('home'))
    
    if db is None: # Added DB check
        return "Database not connected. Please check server logs.", 500
    
    user_phone = session['phone']
    history_tasks = list(db.taskHistory.find({"phone": user_phone}).sort("action_at", -1))
    
    return render_template('history.html', history_tasks=history_tasks)

@app.route('/logout')
def logout():
    session.pop('phone', None)
    session.pop('username', None)
    session.pop('show_intro', None)
    session.pop('urgent_notification_sent_http', None)
    session.pop('urgent_notification_sent_socket_for_this_request', None)
    return redirect(url_for('home'))

if __name__ == '__main__':
    socketio.run(app, debug=True)