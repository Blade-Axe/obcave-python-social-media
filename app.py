from flask import Flask, redirect, request, session, render_template, url_for
import mysql.connector
import requests
import os
from validators import url as validate_url
from flask import jsonify
import json
from json import loads
from flask import send_from_directory
from functools import wraps
from flask import flash, get_flashed_messages
from dataclasses import dataclass
from flask_sqlalchemy import SQLAlchemy

db_sql = SQLAlchemy()

app = Flask(__name__, static_folder='static')
app.secret_key = os.urandom(24)

# Discord OAuth2 credentials
CLIENT_ID = '1332004818918572062'
CLIENT_SECRET = 'Jtujb5ksaobqwbta8yqc20yeezi98fl9'
REDIRECT_URI = 'http://127.0.0.1:5000/callback'
DISCORD_API_URL = 'https://discord.com/api/v10'

# Database connection
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="arvoinc123",
    database="obcave_db"
)
cursor = db.cursor(buffered=True)

@dataclass
class Badge(db_sql.Model):
    id: int
    name: str
    description: str
    icon_path: str
    permission_level: str
    order_rank: int
    is_dynamic: bool

    id = db_sql.Column(db_sql.Integer, primary_key=True)
    name = db_sql.Column(db_sql.String(100), nullable=False)
    description = db_sql.Column(db_sql.Text)
    icon_path = db_sql.Column(db_sql.String(255))
    permission_level = db_sql.Column(db_sql.String(50))
    order_rank = db_sql.Column(db_sql.Integer)
    is_dynamic = db_sql.Column(db_sql.Boolean, default=False)

def validate_links(links):
    if not links:
        return True
    for link in links.split(','):
        if not validate_url(link.strip()):
            return False
    return True

def process_bio_mentions(bio_text):
        """
        Convert @username mentions in bio text to HTML links.
        Returns the processed text with @mentions converted to links.
        """
        if not bio_text:
            return ""
        
        import re
        
        # Pattern to match @username
        # Usernames can contain letters, numbers, underscores, and hyphens
        mention_pattern = r'@([\w-]+)'
        
        def replace_mention(match):
            username = match.group(1)
            # Create a link to the user's profile
            return f'<a href="/@{username}" class="bio-mention">@{username}</a>'
        
        # Replace all @mentions with links
        processed_text = re.sub(mention_pattern, replace_mention, bio_text)
        
        return processed_text

# Permission decorator
def require_permission(level):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            
            cursor.execute("""
                SELECT MAX(
                    CASE badges.permission_level
                        WHEN 'admin' THEN 4
                        WHEN 'moderator' THEN 3
                        WHEN 'obber' THEN 2
                        ELSE 1
                    END
                ) as max_level
                FROM users u
                LEFT JOIN user_badges ub ON u.id = ub.user_id
                JOIN JSON_TABLE(
                    ub.badge_order,
                    '$[*]' COLUMNS (badge_id VARCHAR(255) PATH '$')
                ) badge_nums ON 1=1
                JOIN badges ON badges.id = badge_nums.badge_id
                WHERE u.discord_id = %s
            """, (session['user_id'],))
            result = cursor.fetchone()
            user_level = result[0] if result and result[0] is not None else 1
            
            required_level = {'admin':4, 'moderator':3, 'obber':2, 'member':1, 'cosmetic':1}[level]
            
            if user_level < required_level:
                return "Unauthorized", 403
            return f(*args, **kwargs)
        return decorated
    return decorator

# Admin routes
@app.route('/admin/badges')
@require_permission('admin')
def admin_badges():
    cursor = db.cursor(dictionary=True)  # <-- Use dictionary cursor
    cursor.execute("SELECT * FROM badges ORDER BY order_rank DESC")
    badges = cursor.fetchall()
    cursor.close()  # Important: close the cursor
    return render_template('admin_badges.html', badges=badges)

@app.route('/admin/create_badge', methods=['POST'])
@require_permission('admin')
def create_badge():
    try:
        name = request.form['name']
        description = request.form['description']
        permission_level = request.form['permission_level']
        order_rank = request.form['order_rank']
        is_dynamic = 'is_dynamic' in request.form
        icon_path = request.form['icon_path']

        cursor.execute("""
            INSERT INTO badges (name, description, permission_level, order_rank, is_dynamic, icon_path)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (name, description, permission_level, order_rank, is_dynamic, icon_path))
        db.commit()
        flash('Badge created successfully', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error creating badge: {str(e)}', 'error')
    
    return redirect(url_for('admin_badges'))

@app.route('/admin/assign_badge', methods=['POST'])
@require_permission('admin')
def assign_badge():
    try:
        user_id = request.form['user_id']
        badge_id = request.form['badge_id']
        
        # Get internal user ID
        cursor.execute("SELECT id FROM users WHERE discord_id = %s", (user_id,))
        db_user_id = cursor.fetchone()[0]
        
        # Fetch current badge_order
        cursor.execute("SELECT badge_order FROM user_badges WHERE user_id = %s", (db_user_id,))
        result = cursor.fetchone()
        current_order = json.loads(result[0]) if result and result[0] else []
        
        # Add new badge_id to the list
        if str(badge_id) not in current_order:
            current_order.append(str(badge_id))
        
        # Update badge_order
        cursor.execute("""
            INSERT INTO user_badges (user_id, badge_order)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE badge_order = VALUES(badge_order)
        """, (db_user_id, json.dumps(current_order)))
        
        db.commit()
        flash('Badge assigned successfully', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error assigning badge: {str(e)}', 'error')
    return redirect(url_for('admin_badges'))

@app.route('/admin/remove_badge', methods=['POST'])
@require_permission('admin')
def remove_badge():
    try:
        user_id = request.form['user_id']
        badge_id = request.form['badge_id']
        
        cursor.execute("SELECT id FROM users WHERE discord_id = %s", (user_id,))
        db_user_id = cursor.fetchone()[0]
        
        # Fetch current badge_order
        cursor.execute("SELECT badge_order FROM user_badges WHERE user_id = %s", (db_user_id,))
        result = cursor.fetchone()
        if result and result[0]:
            current_order = json.loads(result[0])
            # Remove badge_id from the list
            current_order = [bid for bid in current_order if bid != str(badge_id)]
            # Update badge_order
            cursor.execute("""
                UPDATE user_badges
                SET badge_order = %s
                WHERE user_id = %s
            """, (json.dumps(current_order), db_user_id))
            db.commit()
            flash('Badge removed successfully', 'success')
        else:
            flash('No badges to remove', 'warning')
    except Exception as e:
        db.rollback()
        flash(f'Error removing badge: {str(e)}', 'error')
    return redirect(url_for('admin_badges'))

# Edit Badge Route
@app.route('/admin/edit-badge/<int:badge_id>', methods=['GET', 'POST'])
@require_permission('admin')
def edit_badge(badge_id):
    if request.method == 'GET':
        # Use dictionary cursor to get named columns
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM badges WHERE id = %s", (badge_id,))
        badge = cursor.fetchone()
        cursor.close()
        
        if not badge:
            flash('Badge not found', 'error')
            return redirect(url_for('admin_badges'))
        return render_template('edit_badge.html', badge=badge)
    
    # Handle POST request for updates
    try:
        name = request.form['name']
        description = request.form['description']
        permission_level = request.form['permission_level']
        order_rank = request.form['order_rank']
        is_dynamic = 'is_dynamic' in request.form
        icon_path = request.form['icon_path']

        cursor = db.cursor()
        
        cursor.execute("""
            UPDATE badges SET
                name = %s,
                description = %s,
                permission_level = %s,
                order_rank = %s,
                is_dynamic = %s,
                icon_path = %s
            WHERE id = %s
        """, (name, description, permission_level, order_rank, is_dynamic, icon_path, badge_id))
        db.commit()
        cursor.close()
        flash('Badge updated successfully', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error updating badge: {str(e)}', 'error')
    
    return redirect(url_for('admin_badges'))

# Delete Badge Route
@app.route('/admin/delete_badge', methods=['POST'])
@require_permission('admin')
def delete_badge():
    try:
        badge_id = request.form['badge_id']
        
        # First update user_badges to remove this badge from all badge_order arrays
        cursor.execute("""
            UPDATE user_badges 
            SET badge_order = JSON_REMOVE(
                badge_order,
                JSON_UNQUOTE(JSON_SEARCH(badge_order, 'one', %s))
            )
            WHERE JSON_SEARCH(badge_order, 'one', %s) IS NOT NULL
        """, (badge_id, badge_id))
        
        # Then delete the badge itself
        cursor.execute("DELETE FROM badges WHERE id = %s", (badge_id,))
        db.commit()
        flash('Badge deleted successfully', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error deleting badge: {str(e)}', 'error')
    
    return redirect(url_for('admin_badges'))

# Admin Users Management
@app.route('/admin/users')
@require_permission('admin')
def admin_users():
    if 'user_id' not in session:
        return redirect(url_for('home'))
    
    user_id = session['user_id']
    
    cursor = db.cursor(dictionary=True)
    
    # Fetch all users
    cursor.execute("""
        SELECT 
            u.discord_id, u.username, u.avatar_url, 
            u.is_banned, u.ban_reason, pc.is_private, pc.custom_name
        FROM users u
        LEFT JOIN profile_customisation pc ON u.id = pc.user_id
    """)
    users = cursor.fetchall()

    # Fetch current user's details
    cursor.execute("""
        SELECT discord_id FROM users WHERE discord_id = %s
    """, (user_id,))
    current_user = cursor.fetchone()

    cursor.close()
    
    return render_template('admin_users.html', users=users, current_user=current_user)

@app.route('/admin/toggle_ban', methods=['POST'])
@require_permission('admin')
def toggle_ban():
    user_id = request.form.get('user_id')
    action = request.form.get('action')
    ban_reason = request.form.get('ban_reason', '').strip()

    if not user_id:
        flash('User ID is required', 'error')
        return redirect(url_for('admin_users'))

    try:
        if action == 'ban':
            if not ban_reason:
                flash('Ban reason is required', 'error')
                return redirect(url_for('admin_users'))
            # Ban user and set profile to private
            cursor.execute("""
                UPDATE users u
                JOIN profile_customisation pc ON u.id = pc.user_id
                SET 
                    u.is_banned = 1,
                    u.ban_reason = %s,
                    pc.is_private = 1
                WHERE u.discord_id = %s
            """, (ban_reason, user_id))
        elif action == 'unban':
            # Unban user (keep privacy setting as-is)
            cursor.execute("""
                UPDATE users
                SET 
                    is_banned = 0,
                    ban_reason = NULL
                WHERE discord_id = %s
            """, (user_id,))
        
        db.commit()
        flash(f'User {action}ned successfully', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error: {str(e)}', 'error')
    
    return redirect(url_for('admin_users'))

# Badges dashboard
@app.route('/dashboard/badges')
def badge_dashboard():
    if 'user_id' not in session:
        return redirect(url_for('home'))

    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT DISTINCT badges.* 
        FROM users u
        LEFT JOIN user_badges ub ON u.id = ub.user_id
        JOIN JSON_TABLE(
            ub.badge_order,
            '$[*]' COLUMNS (badge_id VARCHAR(255) PATH '$')
        ) badge_nums ON 1=1
        JOIN badges ON badges.id = badge_nums.badge_id
        WHERE u.discord_id = %s
        ORDER BY badges.order_rank DESC
    """, (session['user_id'],))
    user_badges = cursor.fetchall()
    cursor.close()

    return render_template('badge_dashboard.html', badges=user_badges)

@app.context_processor
def inject_user():
    user_avatar = None
    is_obber = False
    is_moderator = False
    is_admin = False
    if 'user_id' in session:
        # Fetch user avatar
        cursor.execute("SELECT avatar_url FROM users WHERE discord_id = %s", (session['user_id'],))
        user_data = cursor.fetchone()
        if user_data:
            user_avatar = user_data[0]
        
        # Check admin status - modified query to use JSON_TABLE to unnest the badge_order array
        cursor.execute("""
            SELECT MAX(
                CASE badges.permission_level
                    WHEN 'admin' THEN 4
                    WHEN 'moderator' THEN 3
                    WHEN 'obber' THEN 2
                    ELSE 1
                END
            ) as max_level
            FROM users u
            LEFT JOIN user_badges ub ON u.id = ub.user_id
            JOIN JSON_TABLE(
                ub.badge_order,
                '$[*]' COLUMNS (badge_id VARCHAR(255) PATH '$')
            ) badge_nums ON 1=1
            JOIN badges ON badges.id = badge_nums.badge_id
            WHERE u.discord_id = %s
        """, (session['user_id'],))
        result = cursor.fetchone()
        user_level = result[0] if result and result[0] is not None else 1
        is_obber = (user_level >= 2)
        is_moderator = (user_level >= 3)
        is_admin = (user_level >= 4)
            
    return {
        'logged_in': 'user_id' in session,
        'current_username': session.get('username'),
        'current_user_avatar': user_avatar,
        'is_obber': is_obber,
        'is_moderator': is_moderator,
        'is_admin': is_admin
    }
    
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, 'static'),
        'favicon.ico',
        mimetype='image/vnd.microsoft.icon'
    )

# Homepage
@app.route('/')
def home():
    # Check if the user is logged in
    if 'user_id' in session:
        logged_in = True
    else:
        logged_in = False
    return render_template('index.html', logged_in=logged_in)

# Discord OAuth2 login
@app.route('/login')
def login():
    return redirect(f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify")

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return "Error: No code provided", 400
    data = {
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
        'scope': 'identify'
    }
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    response = requests.post(f"{DISCORD_API_URL}/oauth2/token", data=data, headers=headers)
    credentials = response.json()

    if 'access_token' not in credentials:
        return f"Error: {credentials}", 400

    # Fetch user data from Discord
    headers = {
        'Authorization': f"Bearer {credentials['access_token']}"
    }
    user_response = requests.get(f"{DISCORD_API_URL}/users/@me", headers=headers)
    user_data = user_response.json()

    # Save or update user in the database
    cursor.execute("""
        INSERT INTO users (discord_id, username, avatar_url, access_token, refresh_token)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        username = VALUES(username),
        avatar_url = VALUES(avatar_url),
        access_token = VALUES(access_token),
        refresh_token = VALUES(refresh_token)
    """, (
        user_data['id'],
        user_data['username'],
        f"https://cdn.discordapp.com/avatars/{user_data['id']}/{user_data['avatar']}.png",
        credentials['access_token'],
        credentials['refresh_token']
    ))
    
    cursor.execute("SELECT is_banned FROM users WHERE discord_id = %s", (user_data['id'],))
    banned_status = cursor.fetchone()[0]

    if banned_status == 1:
        session.clear()
        flash('Your account has been banned', 'error')
        return redirect(url_for('home'))
    
    # Get the user's ID from the users table
    cursor.execute("SELECT id FROM users WHERE discord_id = %s", (user_data['id'],))
    user_id = cursor.fetchone()[0]
    
    # Check if profile customization exists
    cursor.execute("SELECT id FROM profile_customisation WHERE user_id = %s", (user_id,))
    if not cursor.fetchone():
        # Only insert if it doesn't exist
        cursor.execute("""
            INSERT INTO profile_customisation (user_id, font)
            VALUES (%s, 'Mulish')
        """, (user_id,))
    
    db.commit()
    
    # Store user ID in session
    session['user_id'] = str(user_data['id'])
    session['username'] = user_data['username']
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('home'))

    user_id = session['user_id']
    
    cursor.execute("""
        SELECT u.username, u.avatar_url, pc.custom_name, pc.bio, pc.background_colour, pc.links,
            pc.font, pc.font_size, pc.text_colour, pc.text_highlight_colour, pc.text_alignment, pc.is_private, pc.module_order, pc.secondary_colour, pc.tertiary_colour, pc.column_amount, pc.link_colour, pc.link_hover_colour
        FROM users u
        LEFT JOIN profile_customisation pc ON u.id = pc.user_id
        WHERE u.discord_id = %s
    """, (user_id,))
    user = cursor.fetchone()  # Fetch the result

    if user:
        user_data = {
            'username': user[0],
            'avatar_url': user[1],
            'custom_name': user[2],
            'bio': user[3],
            'background_colour': user[4],
            'links': user[5],
            'font': user[6],
            'font_size': user[7],
            'text_colour': user[8],
            'text_highlight_colour': user[9],
            'text_alignment': user[10],
            'is_private': user[11],
            'secondary_colour': user[12],
            'tertiary_colour': user[13],
            'column_amount': user[14],
            'link_colour': user[15],
            'link_hover_colour': user[16]
        }
        public_profile_link = url_for('public_profile', username=user[0], _external=True)
        return render_template('dashboard.html', user=user_data, public_profile_link=public_profile_link)
    else:
        return redirect(url_for('home'))

    

@app.route('/@<username>')
def public_profile(username):
    cursor.execute("""
        SELECT 
            u.discord_id, u.username, u.avatar_url, pc.custom_name, pc.bio, 
            pc.background_colour, pc.links, pc.font, pc.font_size, pc.text_colour, 
            pc.text_highlight_colour, pc.text_alignment, pc.secondary_colour, 
            pc.tertiary_colour, pc.column_amount, pc.link_colour, pc.link_hover_colour,
            u.created_at, pc.is_private, pc.module_order,
            ub.badge_order, pc.is_favicon_enabled, u.is_banned, u.ban_reason
        FROM users u
        LEFT JOIN profile_customisation pc ON u.id = pc.user_id
        LEFT JOIN user_badges ub ON u.id = ub.user_id
        WHERE u.username = %s
    """, (username,))
    user = cursor.fetchone()

    if user:
        user_data = {
            'discord_id': user[0],
            'username': user[1],
            'avatar_url': user[2],
            'custom_name': user[3],
            'bio': process_bio_mentions(user[4]),
            'background_colour': user[5],
            'links': user[6],
            'font': user[7],
            'font_size': user[8],
            'text_colour': user[9],
            'text_highlight_colour': user[10],
            'text_alignment': user[11],
            'secondary_colour': user[12],
            'tertiary_colour': user[13],
            'column_amount': user[14],
            'link_colour': user[15],
            'link_hover_colour': user[16],
            'created_at': user[17],
            'is_private': user[18],
            'module_order': user[19],
            'badges': user[20],
            'is_favicon_enabled': user[21],
            'is_banned': user[22],
            'ban_reason': user[23]
        }
        
        # Get the badge order from user_badges
        badge_order = json.loads(user[20]) if user[20] else []
        
        if badge_order:
            # Fetch all badges at once
            placeholders = ','.join(['%s'] * len(badge_order))
            cursor.execute(
                f"""
                SELECT id, name, icon_path, description, is_dynamic 
                FROM badges 
                WHERE id IN ({placeholders})
                """,
                tuple(badge_order)
            )
            badges_data = cursor.fetchall()
            
            # Create a dictionary mapping badge IDs to badge data
            badges_dict = {str(badge[0]): {
                'id': str(badge[0]),
                'name': badge[1],
                'icon_path': badge[2],
                'description': badge[3],
                'is_dynamic': badge[4]
            } for badge in badges_data}
            
            # Order badges according to badge_order
            user_data['badges'] = [badges_dict[str(badge_id)] 
                                 for badge_id in badge_order 
                                 if str(badge_id) in badges_dict]

        # Check if the logged-in user is the owner of this profile
        logged_in_user_id = session.get('user_id')
        is_owner = logged_in_user_id == user_data['discord_id']
        
        if user_data['is_banned']:
            return render_template('banned_profile.html', 
                            ban_reason=user_data['ban_reason'],
                            username=user_data['username'],
                            is_owner=is_owner)
        elif user_data['is_private'] and not is_owner:
            return render_template('private_profile.html', 
                             username=user_data['username'],
                             is_owner=is_owner)

        public_profile_link = url_for('public_profile', username=user[1], _external=True)
        return render_template('public_profile.html', user=user_data, is_owner=is_owner, public_profile_link=public_profile_link)
    else:
        return render_template('user_not_found.html'), 404
    
@app.route('/dashboard/edit-profile')
def edit_profile():
    if 'user_id' not in session:
        return redirect(url_for('home'))

    user_id = session['user_id']
    
    cursor.execute("""
        SELECT pc.custom_name, pc.bio, pc.background_colour, pc.links,
            pc.font, pc.font_size, pc.text_colour, pc.text_highlight_colour, pc.text_alignment, pc.is_private, pc.secondary_colour, pc.tertiary_colour, pc.column_amount, pc.link_colour, pc.link_hover_colour, pc.is_favicon_enabled
        FROM users u
        LEFT JOIN profile_customisation pc ON u.id = pc.user_id
        WHERE u.discord_id = %s
    """, (user_id,))
    user = cursor.fetchone()  # Fetch the result

    if user:
        user_data = {
            'custom_name': user[0],
            'bio': user[1],
            'background_colour': user[2],
            'links': '' if user[3] == 'none' or user[3] is None else user[3],  # Convert 'none' to empty string
            'font': user[4],
            'font_size': user[5],
            'text_colour': user[6],
            'text_highlight_colour': user[7],
            'text_alignment': user[8],
            'is_private': user[9],
            'secondary_colour': user[10],
            'tertiary_colour': user[11],
            'column_amount': user[12],
            'link_colour': user[13],
            'link_hover_colour': user[14],
            'is_favicon_enabled': user[15]
        }
        return render_template('edit_profile.html', user=user_data)
    else:
        return redirect(url_for('home'))
    
@app.route('/dashboard/privacy')
def edit_privacy():
    if 'user_id' not in session:
        return redirect(url_for('home'))

    user_id = session['user_id']
    
    cursor.execute("""
        SELECT pc.is_private
        FROM users u
        LEFT JOIN profile_customisation pc ON u.id = pc.user_id
        WHERE u.discord_id = %s
    """, (user_id,))
    user = cursor.fetchone()  # Fetch the result

    if user:
        user_data = {
            'is_private': user[0]
        }
        return render_template('edit_privacy.html', user=user_data)
    else:
        return redirect(url_for('home'))
    
@app.route('/dashboard/privacy/update', methods=['POST'])
def update_privacy():
    if 'user_id' not in session:
        return redirect(url_for('home'))

    is_private = 'is_private' in request.form
    user_id = session['user_id']

    cursor.execute("""
        UPDATE profile_customisation
        SET is_private = %s
        WHERE user_id = (SELECT id FROM users WHERE discord_id = %s)
    """, (
        is_private,
        user_id
    ))
    db.commit()
    return redirect(url_for('dashboard'))

@app.route('/dashboard/edit-profile/update', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        return redirect(url_for('home'))

    links = request.form.get('links', '').strip()
    
    # Only validate links if there are actually links provided
    if links and links.lower() != 'none':
        if not validate_links(links):
            return "Invalid links provided", 400
    else:
        links = None  # Set to None if empty or 'none'

    is_favicon_enabled = 'is_favicon_enabled' in request.form
    custom_name = request.form.get('custom_name')
    bio = request.form.get('bio')
    background_colour = request.form.get('background_colour', '#feefcd')
    font = request.form.get('font', 'Mulish')
    font_size = request.form.get('font_size', '24')
    text_colour = request.form.get('text_colour', '#000000')
    text_highlight_colour = request.form.get('text_highlight_colour', '#7fd47a')
    text_alignment = request.form.get('text_alignment', 'centre')
    is_private = 'is_private' in request.form
    secondary_colour = request.form.get('secondary_colour', '#fec49a')
    tertiary_colour = request.form.get('tertiary_colour', '#fda372')
    column_amount = request.form.get('column_amount', '2')
    link_colour = request.form.get('link_colour', '#be4737')
    link_hover_colour = request.form.get('link_hover_colour', '#388ae8')
    user_id = session['user_id']

    if font_size and font_size.strip():
        font_size = int(font_size)
    else:
        font_size = None

    cursor.execute("""
        UPDATE profile_customisation
        SET custom_name = %s, bio = %s, background_colour = %s, links = %s,
            font = %s, font_size = %s, text_colour = %s, text_highlight_colour = %s, text_alignment = %s, is_private = %s, secondary_colour = %s, tertiary_colour = %s, column_amount = %s, link_colour = %s, link_hover_colour = %s, is_favicon_enabled = %s
        WHERE user_id = (SELECT id FROM users WHERE discord_id = %s)
    """, (
        custom_name, 
        bio, 
        background_colour, 
        links,
        font, 
        font_size, 
        text_colour, 
        text_highlight_colour, 
        text_alignment, 
        is_private,
        secondary_colour, 
        tertiary_colour,
        column_amount,
        link_colour,
        link_hover_colour,
        is_favicon_enabled,
        user_id
    ))
    db.commit()
    return redirect(url_for('dashboard'))

@app.route('/save-module-order', methods=['POST'])
def save_module_order():
    if 'user_id' not in session:
        return jsonify({'success': False}), 401
    order_data = request.json.get('order', '')
    user_id = session['user_id']
    # Ensure order_data is a string (e.g., "avatar:2,bio:1")
    if isinstance(order_data, list):
        order_data = ",".join(order_data)  # Fallback for edge cases

    cursor.execute("""
        UPDATE profile_customisation
        SET module_order = %s
        WHERE user_id = (SELECT id FROM users WHERE discord_id = %s)
    """, (order_data, user_id))
    db.commit()
    return jsonify({'success': True})

@app.route('/reorder-badges', methods=['POST'])
def reorder_badges():
    if 'user_id' not in session:
        return jsonify({'success': False}), 401
    
    user_id = session['user_id']
    new_order = request.json.get('order', [])  # List of badge IDs in new order

    print("Received new order:", new_order)  # Debugging log

    cursor.execute("SELECT id FROM users WHERE discord_id = %s", (user_id,))
    db_user_id = cursor.fetchone()[0]

    cursor.execute("SELECT badge_order FROM user_badges WHERE user_id = %s", (db_user_id,))
    result = cursor.fetchone()
    if not result or not result[0]:
        print("No badges found for user in DB")  # Debugging log
        return jsonify({'success': False, 'error': 'No badges found'}), 400
    
    current_badges = json.loads(result[0])

    print("Current Badges in DB:", current_badges)  # Debugging log

    if not all(str(bid) in current_badges for bid in new_order):  # Ensure all IDs exist
        print("Invalid badge ID detected")  # Debugging log
        return jsonify({'success': False, 'error': 'Invalid badge ID'}), 400

    # Update badge_order with new order
    cursor.execute("""
        UPDATE user_badges
        SET badge_order = %s
        WHERE user_id = %s
    """, (json.dumps(new_order), db_user_id))
    
    db.commit()
    print("Database updated successfully")  # Debugging log
    return jsonify({'success': True})


@app.route('/members')
def members():
    cursor.execute("""
        SELECT 
            u.username, 
            u.avatar_url,
            COALESCE(pc.background_colour, '#feefcd') as background_colour,
            COALESCE(pc.secondary_colour, '#fec49a') as secondary_colour,
            COALESCE(pc.tertiary_colour, '#fda372') as tertiary_colour,
            COALESCE(pc.text_colour, '#000000') as text_colour,
            COALESCE(pc.link_colour, '#be4737') as link_colour,
            COALESCE(MIN(b.order_rank), 999) as badge_priority,
            COALESCE(ub.badge_order, '[]') as badge_order
        FROM users u
        JOIN profile_customisation pc ON u.id = pc.user_id
        LEFT JOIN user_badges ub ON u.id = ub.user_id
        LEFT JOIN JSON_TABLE(
            ub.badge_order,
            '$[*]' COLUMNS (badge_id VARCHAR(255) PATH '$')
        ) badge_nums ON 1=1
        LEFT JOIN badges b ON badge_nums.badge_id = b.id
        WHERE pc.is_private = 0
        AND u.is_banned = 0
        GROUP BY 
            u.username, 
            u.avatar_url, 
            pc.background_colour,
            pc.secondary_colour,
            pc.tertiary_colour,
            pc.text_colour,
            pc.link_colour,
            ub.badge_order,
            u.created_at
        ORDER BY 
            badge_priority ASC,  
            u.created_at ASC      
    """)
    users = cursor.fetchall()

    user_data = []
    for user in users:
        try:
            badge_ids = json.loads(user[8]) if user[8] else []
        except (json.JSONDecodeError, TypeError):
            badge_ids = []

        badges = []
        if badge_ids:
            # Create a dictionary to store badge paths indexed by badge ID
            badge_paths = {}
            placeholders = ','.join(['%s'] * len(badge_ids))
            cursor.execute(
                f"SELECT id, icon_path FROM badges WHERE id IN ({placeholders})",
                tuple(badge_ids)
            )
            for badge_id, icon_path in cursor.fetchall():
                badge_paths[str(badge_id)] = icon_path
            
            # Add badges in the original order from badge_order
            for badge_id in badge_ids:
                if str(badge_id) in badge_paths:
                    badges.append(badge_paths[str(badge_id)])

        user_data.append({
            'username': user[0],
            'avatar_url': user[1],
            'background_colour': user[2],
            'secondary_colour': user[3],
            'tertiary_colour': user[4],
            'text_colour': user[5],
            'link_colour': user[6],
            'badges': badges,
            'badge_priority': user[7]
        })

    return render_template('members.html', users=user_data)

@app.route('/reset-module-order', methods=['POST'])
def reset_module_order():
    if 'user_id' not in session:
        return jsonify({'success': False}), 401
    
    user_id = session['user_id']
    # Set module_order to default (empty or your default order)
    cursor.execute("""
        UPDATE profile_customisation
        SET column_amount = 2, module_order = 'avatar:1x1x2x1,links:1x2x1x1,bio:2x2x1x2,member_since:1x3x1x1,badges:1x4x1x1'
        WHERE user_id = (SELECT id FROM users WHERE discord_id = %s)
    """, (user_id,))
    db.commit()
    return jsonify({'success': True})

@app.route('/reset-profile-settings', methods=['POST'])
def reset_profile_settings():
    if 'user_id' not in session:
        return jsonify({'success': False}), 401

    user_id = session['user_id']
    # Reset all customization fields to defaults
    cursor.execute("""
        UPDATE profile_customisation
        SET 
            background_colour = '#feefcd',
            secondary_colour = '#fec49a',
            tertiary_colour = '#fda372',
            text_colour = '#000000',
            text_highlight_colour = '#7fd47a',
            link_colour = '#be4737',
            link_hover_colour = '#388ae8',
            font = 'Mulish',
            font_size = 24,
            text_alignment = 'centre',
            is_favicon_enabled = 1
        WHERE user_id = (SELECT id FROM users WHERE discord_id = %s)
    """, (user_id,))
    db.commit()
    return jsonify({'success': True})

@app.route('/test')
def test():
        return render_template('test.html')
    
@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/achievements')
def achievements():
    return render_template('achievements.html')
    
@app.route('/obcavern/ob-census/2025')
def obcensus2025():
    if 'user_id' not in session:
        return redirect(url_for('home'))

    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT DISTINCT badges.* 
        FROM users u
        LEFT JOIN user_badges ub ON u.id = ub.user_id
        JOIN JSON_TABLE(
            ub.badge_order,
            '$[*]' COLUMNS (badge_id VARCHAR(255) PATH '$')
        ) badge_nums ON 1=1
        JOIN badges ON badges.id = badge_nums.badge_id
        WHERE u.discord_id = %s
        ORDER BY badges.order_rank DESC
    """, (session['user_id'],))
    user_badges = cursor.fetchall()
    cursor.close()

    return render_template('obcensus2025.html', badges=user_badges)

@app.route('/obcavern/ob-census')
def ob_dashboard():
    if 'user_id' not in session:
        return redirect(url_for('home'))

    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT DISTINCT badges.* 
        FROM users u
        LEFT JOIN user_badges ub ON u.id = ub.user_id
        JOIN JSON_TABLE(
            ub.badge_order,
            '$[*]' COLUMNS (badge_id VARCHAR(255) PATH '$')
        ) badge_nums ON 1=1
        JOIN badges ON badges.id = badge_nums.badge_id
        WHERE u.discord_id = %s
        ORDER BY badges.order_rank DESC
    """, (session['user_id'],))
    user_badges = cursor.fetchall()
    cursor.close()

    return render_template('obcavern.html', badges=user_badges)
    
@app.route('/api/badge/<badge_id>')
def get_badge(badge_id):
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM badges WHERE id = %s", (badge_id,))
    badge = cursor.fetchone()
    
    if not badge:
        return jsonify({'error': 'Badge not found'}), 404
        
    return jsonify(badge)

@app.route('/logout')

def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True)