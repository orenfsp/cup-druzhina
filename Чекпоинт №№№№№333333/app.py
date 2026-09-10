import os, io, csv, secrets
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, Response, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image
from sqlalchemy import text
from models import db, User, Appeal, Category, Message, InternalNote, ActionLog, Attachment

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'otklik-hackathon-key')

# Подключение к БД: Docker PostgreSQL или локальный fallback
if os.getenv('DB_HOST'):
    DB_USER = os.getenv('DB_USER', 'otklik_user')
    DB_PASS = os.getenv('DB_PASSWORD', 'secret')
    DB_HOST = os.getenv('DB_HOST', 'postgres')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_NAME = os.getenv('DB_NAME', 'otklik')
    app.config['SQLALCHEMY_DATABASE_URI'] = f'postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///appeals.db'

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

# Местное время Екатеринбург / Оренбург (UTC+5)
def get_local_now():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)

FAILED_ATTEMPTS = {}
ALPHABET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'
def generate_track():
    p1 = ''.join(secrets.choice(ALPHABET) for _ in range(4))
    p2 = ''.join(secrets.choice(ALPHABET) for _ in range(4))
    return f'ОТК-{p1}-{p2}'

# Полный список кризисных маркеров
CRISIS_WORDS = [
    "суицид", "суицидальный", "суицидник", "суициднуться", "самоубийство", "самоубийца", "самоубиться",
    "убить себя", "убиваю себя", "убью себя", "покончить с собой", "свести счёты с жизнью", "повеситься",
    "удавиться", "застрелиться", "отравиться", "утопиться", "вскрыть вены", "порезать вены", "прыгнуть с крыши",
    "смерть", "умереть", "сдохнуть", "умри", "не хочу жить", "хочу умереть", "порезался", "режу себя", "селфхарм",
    "убить", "убийство", "убийца", "убью", "зарезать", "изнасилование", "насильник", "пистолет", "автомат",
    "ружьё", "бомба", "террорист", "бьют", "избивают", "нож"
]

def strip_exif_and_save(file_storage, save_path):
    try:
        image = Image.open(file_storage)
        clean_img = Image.new(image.mode, image.size)
        clean_img.putdata(list(image.getdata()))
        clean_img.save(save_path)
    except Exception:
        file_storage.seek(0)
        file_storage.save(save_path)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ---------- Главная страница (Заявитель) ----------
@app.route('/', methods=['GET', 'POST'])
def index():
    categories = Category.query.all()
    
    # Проверка по трек-номеру
    track = request.args.get('track', '').strip()
    if track:
        client_ip = request.remote_addr or '127.0.0.1'
        now_ts = datetime.now().timestamp()
        attempts = [t for t in FAILED_ATTEMPTS.get(client_ip, []) if now_ts - t < 60]
        
        if len(attempts) >= 5:
            flash('Слишком много попыток проверки трек-номера. Подождите 1 минуту.', 'danger')
            return render_template('index.html', categories=categories)

        appeal = Appeal.query.filter_by(track_number=track).first()
        if appeal:
            return render_template('index.html', appeal=appeal, track=track, categories=categories)
        else:
            attempts.append(now_ts)
            FAILED_ATTEMPTS[client_ip] = attempts
            flash('Обращение с таким трек-номером не найдено.', 'danger')

    if request.method == 'POST':
        text_content = request.form.get('text', '').strip()
        if not text_content:
            flash('Пожалуйста, опишите ситуацию своими словами.', 'warning')
            return redirect(url_for('index'))
            
        category_name = request.form.get('category')
        category = Category.query.filter_by(name=category_name).first() if category_name and category_name != 'custom' else None
        
        is_crisis = any(w in text_content.lower() for w in CRISIS_WORDS)
        track = generate_track()
        
        additional_info = {
            'place': request.form.get('place', 'Не указано'),
            'when': request.form.get('when', 'Не указано'),
            'who': request.form.get('who', 'Не указано')
        }
        
        appeal = Appeal(
            track_number=track,
            applicant_type=request.form.get('applicant_type', 'student'),
            text=text_content,
            additional_data=additional_info,
            category_id=category.id if category else None,
            status='new',
            priority='urgent' if is_crisis else 'standard',
            is_crisis=is_crisis,
            emergency_contact=request.form.get('emergency_contact'),
            created_at=get_local_now(),
            updated_at=get_local_now()
        )
        db.session.add(appeal)
        db.session.flush()

        # Сохранение до 5 вложений с очисткой EXIF
        files = request.files.getlist('attachments')
        for file in files[:5]:
            if file and file.filename:
                safe_name = f"{track}_{secrets.token_hex(4)}.png"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
                strip_exif_and_save(file, file_path)
                db.session.add(Attachment(appeal_id=appeal.id, filename=safe_name))

        db.session.commit()
        flash(f'Обращение принято! Сохраните трек-номер: {track}', 'success')
        return redirect(url_for('index', track=track))

    return render_template('index.html', categories=categories)

# ---------- Ветка «Это помогло / Не помогло» (Оценка обязательна!) ----------
@app.route('/appeal/<track>/feedback', methods=['POST'])
def appeal_feedback(track):
    appeal = Appeal.query.filter_by(track_number=track).first_or_404()
    action = request.form.get('action')

    if action == 'helped':
        rating = request.form.get('rating')
        if not rating or not (1 <= int(rating) <= 5):
            flash('Пожалуйста, обязательно поставьте оценку от 1 до 5 звёзд!', 'warning')
            return redirect(url_for('index', track=track))
        appeal.rating = int(rating)
        appeal.status = 'completed'
        appeal.closed_at = get_local_now()
        appeal.updated_at = get_local_now()
        db.session.commit()
        flash('Спасибо за оценку работы специалиста! Обращение успешно завершено.', 'success')
    elif action == 'not_helped':
        if appeal.return_count >= 2:
            flash('Лимит возвратов исчерпан. Пожалуйста, обратитесь на горячую линию.', 'warning')
        else:
            appeal.status = 'returned'
            appeal.return_count += 1
            appeal.return_reason = request.form.get('reason', 'Заявитель сообщил, что совет не помог')
            appeal.responsible_expert_id = None  # Возвращается оператору!
            appeal.updated_at = get_local_now()
            db.session.commit()
            flash('Обращение передано оператору на пересмотр.', 'info')
    return redirect(url_for('index', track=track))

# ---------- Чат со стороны заявителя ----------
@app.route('/appeal/<track>/message', methods=['POST'])
def send_appeal_message(track):
    appeal = Appeal.query.filter_by(track_number=track).first_or_404()
    text_content = request.form.get('text', '').strip()
    if text_content:
        msg = Message(appeal_id=appeal.id, sender_id=None, is_from_expert=False, text=text_content, created_at=get_local_now())
        appeal.updated_at = get_local_now()
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

        def check_pass(stored, entered):
            if stored == entered: return True
            try: return check_password_hash(stored, entered)
            except: return False

        if user and check_pass(user.password, password):
            login_user(user)
            if user.role == 'operator': return redirect(url_for('operator_dashboard'))
            if user.role == 'expert': return redirect(url_for('expert_dashboard'))
            if user.role == 'admin': return redirect(url_for('admin_dashboard'))
            return redirect(url_for('index'))
        flash('Неверное имя пользователя или пароль', 'login_error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

# ---------- Панель оператора ----------
@app.route('/operator')
@login_required
def operator_dashboard():
    if current_user.role != 'operator': abort(403)
    new_appeals = Appeal.query.filter(Appeal.status.in_(['new', 'returned']))\
        .order_by(Appeal.is_crisis.desc(), Appeal.priority.desc(), Appeal.created_at.asc()).all()
    active_appeals = Appeal.query.filter(Appeal.status.in_(['distributed', 'in_progress', 'needs_clarification', 'ready'])).all()
    experts = User.query.filter_by(role='expert').all()
    return render_template('operator_dashboard.html', appeals=new_appeals, active_appeals=active_appeals, experts=experts, now=get_local_now())

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
        appeal.updated_at = get_local_now()
    if priority:
        appeal.priority = priority
    db.session.commit()
    flash(f'Обращение {appeal.track_number} передано специалисту.', 'success')
    return redirect(url_for('operator_dashboard'))

@app.route('/operator/reject/<int:appeal_id>', methods=['POST'])
@login_required
def operator_reject(appeal_id):
    if current_user.role != 'operator': abort(403)
    appeal = Appeal.query.get_or_404(appeal_id)
    reason = request.form.get('reason', 'Вне компетенции платформы')
    appeal.status = 'rejected'
    appeal.operator_id = current_user.id
    appeal.updated_at = get_local_now()
    appeal.additional_data = dict(appeal.additional_data or {}, rejection_reason=reason)
    db.session.commit()
    flash(f'Обращение {appeal.track_number} отклонено как спам.', 'warning')
    return redirect(url_for('operator_dashboard'))

# ---------- Панель эксперта (Передача + Соисполнитель) ----------
@app.route('/expert')
@login_required
def expert_dashboard():
    if current_user.role != 'expert': abort(403)
    # Эксперт видит дела, где он ответственный ИЛИ соисполнитель
    my_appeals = Appeal.query.filter((Appeal.responsible_expert_id == current_user.id) | (Appeal.co_expert_id == current_user.id))\
        .filter(Appeal.status.in_(['distributed', 'in_progress', 'needs_clarification', 'ready']))\
        .order_by(Appeal.priority.desc(), Appeal.created_at.asc()).all()
    colleagues = User.query.filter(User.role == 'expert', User.id != current_user.id).all()
    return render_template('expert_dashboard.html', appeals=my_appeals, colleagues=colleagues)

@app.route('/expert/take/<int:appeal_id>', methods=['POST'])
@login_required
def expert_take(appeal_id):
    if current_user.role != 'expert': abort(403)
    appeal = Appeal.query.get_or_404(appeal_id)
    appeal.status = 'in_progress'
    appeal.updated_at = get_local_now()
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
        appeal.updated_at = get_local_now()
        db.session.commit()
        flash('Рекомендация передана заявителю (статус "Ответ готов").', 'success')
    else:
        flash('Текст рекомендации не может быть пустым.', 'warning')
    return redirect(url_for('expert_dashboard'))

@app.route('/expert/message/<int:appeal_id>', methods=['POST'])
@login_required
def expert_send_message(appeal_id):
    if current_user.role != 'expert': abort(403)
    appeal = Appeal.query.get_or_404(appeal_id)
    text_content = request.form.get('text', '').strip()
    if text_content:
        msg = Message(appeal_id=appeal.id, sender_id=current_user.id, is_from_expert=True, text=text_content, created_at=get_local_now())
        appeal.status = 'needs_clarification'
        appeal.updated_at = get_local_now()
        db.session.add(msg)
        db.session.commit()
        flash('Вопрос отправлен заявителю.', 'success')
    return redirect(url_for('expert_dashboard'))

@app.route('/expert/note/<int:appeal_id>', methods=['POST'])
@login_required
def expert_add_note(appeal_id):
    if current_user.role != 'expert': abort(403)
    appeal = Appeal.query.get_or_404(appeal_id)
    note_text = request.form.get('note', '').strip()
    if note_text:
        note = InternalNote(appeal_id=appeal.id, author_id=current_user.id, text=note_text, created_at=get_local_now())
        db.session.add(note)
        db.session.commit()
        flash('Внутренняя заметка добавлена (заявитель её не видит).', 'success')
    return redirect(url_for('expert_dashboard'))

# Передача дела другому эксперту (С4.5)
@app.route('/expert/transfer/<int:appeal_id>', methods=['POST'])
@login_required
def expert_request_transfer(appeal_id):
    if current_user.role != 'expert': abort(403)
    appeal = Appeal.query.get_or_404(appeal_id)
    target_expert_id = request.form.get('target_expert_id')
    reason = request.form.get('reason', '').strip()
    
    if target_expert_id:
        appeal.responsible_expert_id = int(target_expert_id)
        appeal.status = 'distributed'
        appeal.transfer_reason = reason
        appeal.updated_at = get_local_now()
        db.session.commit()
        flash('Обращение успешно передано выбранному эксперту.', 'success')
    return redirect(url_for('expert_dashboard'))

# Подключение соисполнителя (С4.4)
@app.route('/expert/coexpert/<int:appeal_id>', methods=['POST'])
@login_required
def expert_add_coexpert(appeal_id):
    if current_user.role != 'expert': abort(403)
    appeal = Appeal.query.get_or_404(appeal_id)
    co_id = request.form.get('co_expert_id')
    if co_id:
        appeal.co_expert_id = int(co_id)
        appeal.updated_at = get_local_now()
        db.session.commit()
        flash('Второй специалист подключен как соисполнитель (оба могут работать с делом).', 'success')
    return redirect(url_for('expert_dashboard'))

# ---------- Панель администратора (С7, С8, CRUD пользователей) ----------
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
    
    # Средняя оценка
    rated_appeals = Appeal.query.filter(Appeal.rating.isnot(None)).all()
    avg_rating = round(sum(a.rating for a in rated_appeals) / len(rated_appeals), 1) if rated_appeals else '—'
    
    stuck_appeals = Appeal.query.filter(Appeal.status.in_(['distributed', 'in_progress', 'needs_clarification'])).all()
    return render_template('admin_dashboard.html', categories=categories, users=users, total=total, by_status=by_status, avg_rating=avg_rating, stuck_appeals=stuck_appeals, now=get_local_now())

# С7.3: Добавление сотрудника
@app.route('/admin/user/add', methods=['POST'])
@login_required
def admin_add_user():
    if current_user.role != 'admin': abort(403)
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    role = request.form.get('role')
    specialization = request.form.get('specialization') or None
    max_cases = int(request.form.get('max_cases', 5))
    
    if User.query.filter_by(username=username).first():
        flash('Пользователь с таким логином уже существует!', 'danger')
        return redirect(url_for('admin_dashboard'))
        
    new_u = User(username=username, password=generate_password_hash(password), role=role, specialization=specialization, max_active_cases=max_cases)
    db.session.add(new_u)
    db.session.commit()
    flash(f'Сотрудник {username} ({role}) успешно создан.', 'success')
    return redirect(url_for('admin_dashboard'))

# С7.3: Редактирование сотрудника (смена пароля/должности)
@app.route('/admin/user/edit/<int:user_id>', methods=['POST'])
@login_required
def admin_edit_user(user_id):
    if current_user.role != 'admin': abort(403)
    u = User.query.get_or_404(user_id)
    new_password = request.form.get('password', '').strip()
    u.role = request.form.get('role')
    u.specialization = request.form.get('specialization') or None
    u.max_active_cases = int(request.form.get('max_cases', 5))
    if new_password:
        u.password = generate_password_hash(new_password)
    db.session.commit()
    flash(f'Данные сотрудника {u.username} обновлены.', 'success')
    return redirect(url_for('admin_dashboard'))

# С7.3: Удаление сотрудника
@app.route('/admin/user/delete/<int:user_id>', methods=['POST'])
@login_required
def admin_delete_user(user_id):
    if current_user.role != 'admin': abort(403)
    if user_id == current_user.id:
        flash('Нельзя удалить самого себя!', 'danger')
        return redirect(url_for('admin_dashboard'))
    u = User.query.get_or_404(user_id)
    db.session.delete(u)
    db.session.commit()
    flash('Сотрудник удален.', 'warning')
    return redirect(url_for('admin_dashboard'))

# С7.5: Разблокировка зависшего тикета с записью в аудит-лог
@app.route('/admin/override/<int:appeal_id>', methods=['POST'])
@login_required
def admin_override_appeal(appeal_id):
    if current_user.role != 'admin': abort(403)
    appeal = Appeal.query.get_or_404(appeal_id)
    new_status = request.form.get('new_status')
    reason = request.form.get('reason', '').strip()
    if not reason:
        flash('Причина вмешательства обязательна!', 'danger')
        return redirect(url_for('admin_dashboard'))
    appeal.status = new_status
    appeal.updated_at = get_local_now()
    db.session.add(ActionLog(appeal_id=appeal.id, user_id=current_user.id, action=f"Смена статуса на {new_status}", reason=reason, created_at=get_local_now()))
    db.session.commit()
    flash(f'Обращение {appeal.track_number} разблокировано. Запись внесена в аудит-лог.', 'success')
    return redirect(url_for('admin_dashboard'))

# С8: Обезличенный датасет в CSV
@app.route('/admin/export/csv')
@login_required
def admin_export_csv():
    if current_user.role != 'admin': abort(403)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'ID_Трек', 'Роль_заявителя', 'Категория', 'Статус', 'Приоритет', 'Кризис_флаг',
        'Количество_возвратов', 'Обязательная_оценка', 'Причина_отказа_или_возврата',
        'Ответственный_специалист', 'Соисполнитель', 'Создано_ЕКБ', 'Закрыто_ЕКБ'
    ])
    for a in Appeal.query.all():
        reason_info = a.return_reason or (a.additional_data.get('rejection_reason') if a.additional_data else '') or ''
        writer.writerow([
            a.track_number, a.applicant_type,
            a.category.name if a.category else 'Без категории',
            a.status, a.priority, a.is_crisis, a.return_count,
            a.rating if a.rating else 'Не закрыто',
            reason_info,
            f"{a.expert.username} ({a.expert.specialization or a.expert.role})" if a.expert else 'Не назначен',
            f"{a.co_expert.username} ({a.co_expert.specialization})" if a.co_expert else 'Нет',
            a.created_at.strftime('%d.%m.%Y %H:%M'),
            a.closed_at.strftime('%d.%m.%Y %H:%M') if a.closed_at else 'В процессе'
        ])
    output.seek(0)
    return Response(output.getvalue().encode('utf-8-sig'), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=otklik_dataset.csv"})

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

# Инициализация с безопасными миграциями и 4 профилями специалистов
def init_db():
    with app.app_context():
        db.create_all()
        # Автомиграция для PostgreSQL (добавляет недостающие колонки без сброса базы!)
        if os.getenv('DB_HOST'):
            try:
                db.session.execute(text("ALTER TABLE appeals ADD COLUMN IF NOT EXISTS co_expert_id INT REFERENCES users(id);"))
                db.session.execute(text("ALTER TABLE appeals ADD COLUMN IF NOT EXISTS transfer_requested BOOLEAN DEFAULT FALSE;"))
                db.session.execute(text("ALTER TABLE appeals ADD COLUMN IF NOT EXISTS transfer_reason TEXT;"))
                db.session.commit()
            except Exception:
                db.session.rollback()

        if Category.query.count() == 0:
            cats = [
                ('Травля и оскорбления', 'psychologist'),
                ('Конфликт с одноклассниками', 'conflictologist'),
                ('Кибербуллинг', 'psychologist'),
                ('Давление и угрозы', 'lawyer'),
                ('Конфликт с учителем', 'conflictologist'),
                ('Конфликт с родителями', 'social_pedagogue'),
                ('Вопрос юридического характера', 'lawyer'),
                ('Не знаю, как это назвать', 'psychologist')
            ]
            for c_name, c_grp in cats: db.session.add(Category(name=c_name, expert_group=c_grp))
            db.session.commit()
            
        # Сиды пользователей: Администратор, Оператор, 4 профильных эксперта
        if User.query.count() == 0:
            db.session.add_all([
                User(username='admin', password=generate_password_hash('admin123'), role='admin'),
                User(username='operator', password=generate_password_hash('operator123'), role='operator'),
                User(username='psychologist', password=generate_password_hash('expert123'), role='expert', specialization='Психолог'),
                User(username='lawyer', password=generate_password_hash('expert123'), role='expert', specialization='Юрист'),
                User(username='conflictologist', password=generate_password_hash('expert123'), role='expert', specialization='Конфликтолог'),
                User(username='social_pedagogue', password=generate_password_hash('expert123'), role='expert', specialization='Социальный педагог')
            ])
            db.session.commit()

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)