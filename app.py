import os, io, csv, secrets
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image
from models import db, User, Appeal, Category, Message, InternalNote, ActionLog, Attachment

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'otklik-hackathon-key')

# Подключение к PostgreSQL (в Docker или локально)
DB_USER = os.getenv('DB_USER', 'otklik_user')
DB_PASS = os.getenv('DB_PASSWORD', 'secret')
DB_HOST = os.getenv('DB_HOST', 'postgres')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'otklik')

app.config['SQLALCHEMY_DATABASE_URI'] = f'postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# С5.5: Защита от перебора трек-номеров (rate limiting)
FAILED_ATTEMPTS = {}

# Криптостойкий трек-номер без 0/O и 1/I/L
ALPHABET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'
def generate_track():
    p1 = ''.join(secrets.choice(ALPHABET) for _ in range(4))
    p2 = ''.join(secrets.choice(ALPHABET) for _ in range(4))
    return f'ОТК-{p1}-{p2}'

CRISIS_WORDS = ['суицид', 'убить', 'убью', 'покончить', 'смерт', 'повесит', 'вскрыть', 'нож', 'избива', 'режу', 'бьют', 'насили']

def strip_exif_and_save(file_storage, save_path):
    """Раздел 6 ТЗ: Очистка EXIF и GPS-данных с изображений"""
    try:
        image = Image.open(file_storage)
        clean_img = Image.new(image.mode, image.size)
        clean_img.putdata(list(image.getdata()))
        clean_img.save(save_path)
    except Exception:
        file_storage.seek(0)
        file_storage.save(save_path)

# ---------- Главная страница (С1, С2, С5.5, С6) ----------
@app.route('/', methods=['GET', 'POST'])
def index():
    categories = Category.query.all()
    
    # Проверка трек-номера заявителем
    track = request.args.get('track', '').strip()
    if track:
        client_ip = request.remote_addr or '127.0.0.1'
        now = datetime.now().timestamp()
        attempts = [t for t in FAILED_ATTEMPTS.get(client_ip, []) if now - t < 60]
        
        # С5.5: Не более 5 попыток в минуту
        if len(attempts) >= 5:
            flash('Слишком много попыток проверки. В целях безопасности подождите 1 минуту.', 'danger')
            return render_template('index.html', categories=categories)

        appeal = Appeal.query.filter_by(track_number=track).first()
        if appeal:
            return render_template('index.html', appeal=appeal, track=track, categories=categories)
        else:
            attempts.append(now)
            FAILED_ATTEMPTS[client_ip] = attempts
            flash('Обращение с таким трек-номером не найдено.', 'danger')

    # Подача обращения
    if request.method == 'POST':
        text = request.form.get('text', '').strip()
        if not text:
            flash('Пожалуйста, расскажи о ситуации своими словами.', 'warning')
            return redirect(url_for('index'))
            
        category_name = request.form.get('category')
        category = Category.query.filter_by(name=category_name).first() if category_name and category_name != 'custom' else None
        
        is_crisis = any(w in text.lower() for w in CRISIS_WORDS)
        track = generate_track()
        
        appeal = Appeal(
            track_number=track,
            applicant_type=request.form.get('applicant_type', 'student'),
            text=text,
            additional_data={'place': request.form.get('place')},
            category_id=category.id if category else None,
            status='new',
            priority='urgent' if is_crisis else 'standard',
            is_crisis=is_crisis,
            emergency_contact=request.form.get('emergency_contact')
        )
        db.session.add(appeal)
        db.session.flush()

        # Очистка EXIF для файлов
        files = request.files.getlist('attachments')
        for file in files[:5]:
            if file and file.filename:
                safe_name = f"{track}_{secrets.token_hex(4)}.png"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
                strip_exif_and_save(file, file_path)
                db.session.add(Attachment(appeal_id=appeal.id, filename=safe_name))

        db.session.commit()
        flash(f'Обращение принято. Сохраните трек-номер: {track}', 'success')
        return redirect(url_for('index', track=track))

    return render_template('index.html', categories=categories)

# ---------- С5: Ветка обратной связи («Это помогло» / «Это не помогло») ----------
@app.route('/appeal/<track>/feedback', methods=['POST'])
def appeal_feedback(track):
    appeal = Appeal.query.filter_by(track_number=track).first_or_404()
    action = request.form.get('action')

    if action == 'helped':
        appeal.status = 'completed'
        appeal.rating = int(request.form.get('rating', 5))
        appeal.closed_at = datetime.now()
        db.session.commit()
        flash('Рады, что смогли помочь! Обращение успешно завершено.', 'success')
    elif action == 'not_helped':
        if appeal.return_count >= 2:
            flash('Лимит повторных возвратов исчерпан. Пожалуйста, обратитесь на горячую линию.', 'warning')
        else:
            appeal.status = 'returned'
            appeal.return_count += 1
            appeal.return_reason = request.form.get('reason', 'Заявитель указал, что помощь не решила проблему')
            appeal.responsible_expert_id = None  # Возврат оператору!
            db.session.commit()
            flash('Обращение возвращено оператору для смены специалиста.', 'info')
    return redirect(url_for('index', track=track))

# ---------- Отправка сообщения заявителем ----------
@app.route('/appeal/<track>/message', methods=['POST'])
def send_appeal_message(track):
    appeal = Appeal.query.filter_by(track_number=track).first_or_404()
    text = request.form.get('text', '').strip()
    if text:
        msg = Message(appeal_id=appeal.id, sender_id=None, is_from_expert=False, text=text)
        db.session.add(msg)
        db.session.commit()
        flash('Сообщение отправлено специалисту.', 'success')
    return redirect(url_for('index', track=track))

# ---------- Авторизация сотрудников ----------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user = User.query.filter_by(username=username).first()

        def check_pass(stored_pass, entered_pass):
            if stored_pass == entered_pass: return True
            try: return check_password_hash(stored_pass, entered_pass)
            except: return False

        if user and check_pass(user.password, password):
            login_user(user)
            if user.role == 'operator': return redirect(url_for('operator_dashboard'))
            if user.role == 'expert': return redirect(url_for('expert_dashboard'))
            if user.role == 'admin': return redirect(url_for('admin_dashboard'))
            return redirect(url_for('index'))
        flash('Неверное имя пользователя или пароль', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

# ---------- Панель оператора (С3, С6) ----------
@app.route('/operator')
@login_required
def operator_dashboard():
    if current_user.role != 'operator': abort(403)
    # Кризисные на первом месте, затем возвращённые, затем по дате
    new_appeals = Appeal.query.filter(Appeal.status.in_(['new', 'returned']))\
        .order_by(Appeal.is_crisis.desc(), Appeal.priority.desc(), Appeal.created_at.asc()).all()
    experts = User.query.filter_by(role='expert').all()
    return render_template('operator_dashboard.html', appeals=new_appeals, experts=experts)

@app.route('/operator/assign/<int:appeal_id>', methods=['POST'])
@login_required
def operator_assign(appeal_id):
    if current_user.role != 'operator': abort(403)
    appeal = Appeal.query.get_or_404(appeal_id)
    expert_id = request.form.get('expert_id')
    priority = request.form.get('priority')
    if expert_id:
        appeal.responsible_expert_id = int(expert_id)
        appeal.status = 'distributed'
        appeal.operator_id = current_user.id
    if priority:
        appeal.priority = priority
    db.session.commit()
    flash(f'Обращение {appeal.track_number} передано эксперту.', 'success')
    return redirect(url_for('operator_dashboard'))

@app.route('/operator/reject/<int:appeal_id>', methods=['POST'])
@login_required
def operator_reject(appeal_id):
    if current_user.role != 'operator': abort(403)
    appeal = Appeal.query.get_or_404(appeal_id)
    reason = request.form.get('reason', 'Вне компетенции платформы')
    appeal.status = 'rejected'
    appeal.operator_id = current_user.id
    appeal.additional_data = dict(appeal.additional_data or {}, rejection_reason=reason)
    db.session.commit()
    flash(f'Обращение {appeal.track_number} отклонено.', 'warning')
    return redirect(url_for('operator_dashboard'))

# ---------- Панель эксперта (С4) ----------
@app.route('/expert')
@login_required
def expert_dashboard():
    if current_user.role != 'expert': abort(403)
    my_appeals = Appeal.query.filter_by(responsible_expert_id=current_user.id)\
        .filter(Appeal.status.in_(['distributed', 'in_progress', 'needs_clarification', 'ready']))\
        .order_by(Appeal.priority.desc(), Appeal.created_at.asc()).all()
    return render_template('expert_dashboard.html', appeals=my_appeals)

@app.route('/expert/take/<int:appeal_id>', methods=['POST'])
@login_required
def expert_take(appeal_id):
    if current_user.role != 'expert': abort(403)
    appeal = Appeal.query.get_or_404(appeal_id)
    appeal.status = 'in_progress'
    db.session.commit()
    flash('Обращение взято в работу.', 'success')
    return redirect(url_for('expert_dashboard'))

@app.route('/expert/recommendation/<int:appeal_id>', methods=['POST'])
@login_required
def expert_give_recommendation(appeal_id):
    if current_user.role != 'expert': abort(403)
    appeal = Appeal.query.get_or_404(appeal_id)
    rec = request.form.get('recommendation', '').strip()
    if rec:
        appeal.recommendation = rec
        appeal.status = 'ready'
        db.session.commit()
        flash('Рекомендация передана заявителю (статус "Ответ готов").', 'success')
    return redirect(url_for('expert_dashboard'))

@app.route('/expert/message/<int:appeal_id>', methods=['POST'])
@login_required
def expert_send_message(appeal_id):
    if current_user.role != 'expert': abort(403)
    appeal = Appeal.query.get_or_404(appeal_id)
    text = request.form.get('text', '').strip()
    if text:
        msg = Message(appeal_id=appeal.id, sender_id=current_user.id, is_from_expert=True, text=text)
        appeal.status = 'needs_clarification'
        db.session.add(msg)
        db.session.commit()
        flash('Вопрос отправлен заявителю.', 'success')
    return redirect(url_for('expert_dashboard'))

@app.route('/expert/note/<int:appeal_id>', methods=['POST'])
@login_required
def expert_add_note(appeal_id):
    if current_user.role != 'expert': abort(403)
    appeal = Appeal.query.get_or_404(appeal_id)
    text = request.form.get('note', '').strip()
    if text:
        note = InternalNote(appeal_id=appeal.id, author_id=current_user.id, text=text)
        db.session.add(note)
        db.session.commit()
        flash('Внутренняя заметка добавлена (заявитель её не видит).', 'success')
    return redirect(url_for('expert_dashboard'))

# ---------- Панель администратора (С7, С8) ----------
@app.route('/admin')
@login_required
def admin_dashboard():
    if current_user.role != 'admin': abort(403)
    categories = Category.query.all()
    users = User.query.all()
    total = Appeal.query.count()
    by_status = {st: Appeal.query.filter_by(status=st).count() for st in [
        'new', 'distributed', 'in_progress', 'needs_clarification', 'ready', 'returned', 'completed', 'rejected'
    ]}
    stuck_appeals = Appeal.query.filter(Appeal.status.in_(['distributed', 'in_progress', 'needs_clarification'])).all()
    return render_template('admin_dashboard.html', categories=categories, users=users, total=total, by_status=by_status, stuck_appeals=stuck_appeals)

@app.route('/admin/override/<int:appeal_id>', methods=['POST'])
@login_required
def admin_override_appeal(appeal_id):
    """С7.5: Ручная разблокировка зависшего тикета с причиной в аудит-лог"""
    if current_user.role != 'admin': abort(403)
    appeal = Appeal.query.get_or_404(appeal_id)
    new_status = request.form.get('new_status')
    reason = request.form.get('reason', '').strip()
    if not reason:
        flash('Причина ручного вмешательства обязательна!', 'danger')
        return redirect(url_for('admin_dashboard'))
    appeal.status = new_status
    db.session.add(ActionLog(appeal_id=appeal.id, user_id=current_user.id, action=f"Смена статуса на {new_status}", reason=reason))
    db.session.commit()
    flash(f'Статус обращения {appeal.track_number} изменён. Запись внесена в аудит-лог.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/export/csv')
@login_required
def admin_export_csv():
    """С8: Обезличенный экспорт СТРОГО БЕЗ текстов"""
    if current_user.role != 'admin': abort(403)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Трек-номер', 'Роль заявителя', 'Категория', 'Статус', 'Приоритет', 'Кризис', 'Возвратов', 'Создано'])
    for a in Appeal.query.all():
        writer.writerow([
            a.track_number, a.applicant_type,
            a.category.name if a.category else 'Без категории',
            a.status, a.priority, a.is_crisis, a.return_count, a.created_at.strftime('%Y-%m-%d %H:%M')
        ])
    output.seek(0)
    return Response(output.getvalue().encode('utf-8-sig'), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=otklik_analytics.csv"})

@app.route('/admin/category/add', methods=['POST'])
@login_required
def admin_add_category():
    if current_user.role != 'admin': abort(403)
    name = request.form.get('name', '').strip()
    if name:
        cat = Category(name=name, expert_group=request.form.get('expert_group', 'psychologist'))
        db.session.add(cat)
        db.session.commit()
        flash(f'Категория "{name}" добавлена.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/category/delete/<int:cat_id>', methods=['POST'])
@login_required
def admin_delete_category(cat_id):
    if current_user.role != 'admin': abort(403)
    cat = Category.query.get_or_404(cat_id)
    db.session.delete(cat)
    db.session.commit()
    flash('Категория удалена.', 'warning')
    return redirect(url_for('admin_dashboard'))

def init_db():
    with app.app_context():
        db.create_all()
        if Category.query.count() == 0:
            cats = [
                'Травля и оскорбления', 'Конфликт с одноклассниками', 'Кибербуллинг',
                'Давление и угрозы', 'Конфликт с учителем', 'Конфликт с родителями',
                'Вопрос юридического характера', 'Не знаю, как это назвать'
            ]
            for c in cats: db.session.add(Category(name=c, expert_group='psychologist'))
            db.session.commit()
        if User.query.count() == 0:
            db.session.add_all([
                User(username='admin', password=generate_password_hash('admin123'), role='admin'),
                User(username='operator', password=generate_password_hash('operator123'), role='operator'),
                User(username='psychologist', password=generate_password_hash('expert123'), role='expert', specialization='Психолог'),
                User(username='lawyer', password=generate_password_hash('expert123'), role='expert', specialization='Юрист')
            ])
            db.session.commit()

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)