from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'operator', 'expert', 'admin'
    specialization = db.Column(db.String(100), nullable=True)
    max_active_cases = db.Column(db.Integer, default=5)

    assigned_appeals = db.relationship('Appeal', foreign_keys='Appeal.responsible_expert_id', back_populates='expert')
    operator_appeals = db.relationship('Appeal', foreign_keys='Appeal.operator_id', back_populates='operator_user')


class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    expert_group = db.Column(db.String(100), default='psychologist')
    keywords = db.Column(db.Text, nullable=True)

    appeals = db.relationship('Appeal', back_populates='category')


class Appeal(db.Model):
    __tablename__ = 'appeals'
    id = db.Column(db.Integer, primary_key=True)
    track_number = db.Column(db.String(32), unique=True, nullable=False, index=True)
    applicant_type = db.Column(db.String(20), nullable=False)  # 'student', 'parent', 'teacher'
    text = db.Column(db.Text, nullable=False)
    additional_data = db.Column(db.JSON, default={})
    
    status = db.Column(db.String(30), default='new', index=True)
    priority = db.Column(db.String(20), default='standard', index=True)
    is_crisis = db.Column(db.Boolean, default=False, index=True)
    emergency_contact = db.Column(db.String(150), nullable=True)  # С6: Добровольный контакт
    
    recommendation = db.Column(db.Text, nullable=True)            # С4.6: Финальный совет эксперта
    return_count = db.Column(db.Integer, default=0)               # С5: Лимит 2 возврата
    return_reason = db.Column(db.Text, nullable=True)
    rating = db.Column(db.Integer, nullable=True)                 # Оценка 1-5
    
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    closed_at = db.Column(db.DateTime, nullable=True)

    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    responsible_expert_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    operator_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    category = db.relationship('Category', foreign_keys=[category_id], back_populates='appeals')
    expert = db.relationship('User', foreign_keys=[responsible_expert_id], back_populates='assigned_appeals')
    operator_user = db.relationship('User', foreign_keys=[operator_id], back_populates='operator_appeals')

    messages = db.relationship('Message', back_populates='appeal_ref', lazy='dynamic', cascade="all, delete-orphan")
    internal_notes = db.relationship('InternalNote', back_populates='appeal_ref', lazy='dynamic', cascade="all, delete-orphan")
    logs = db.relationship('ActionLog', back_populates='appeal_ref', lazy='dynamic', cascade="all, delete-orphan")
    attachments = db.relationship('Attachment', back_populates='appeal_ref', lazy='dynamic', cascade="all, delete-orphan")

    @property
    def status_display(self):
        statuses = {
            'new': 'Мы получили обращение',
            'distributed': 'Передано специалисту',
            'in_progress': 'Специалист разбирается в ситуации',
            'needs_clarification': 'Специалист задал вопрос',
            'ready': 'Ответ готов',
            'returned': 'Возвращено на доработку',
            'completed': 'Рады, что смогли помочь',
            'rejected': 'Отклонено вне компетенции',
            'closed_no_answer': 'Закрыто без ответа'
        }
        return statuses.get(self.status, self.status)

    @property
    def priority_display(self):
        priorities = {'low': 'Низкий', 'standard': 'Стандартный', 'urgent': 'Срочно'}
        return priorities.get(self.priority, self.priority)

    @property
    def applicant_type_display(self):
        types = {'student': 'Школьник', 'parent': 'Родитель', 'teacher': 'Педагог'}
        return types.get(self.applicant_type, self.applicant_type)


class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    appeal_id = db.Column(db.Integer, db.ForeignKey('appeals.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    is_from_expert = db.Column(db.Boolean, default=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    is_internal = db.Column(db.Boolean, default=False)

    sender = db.relationship('User', foreign_keys=[sender_id])
    appeal_ref = db.relationship('Appeal', back_populates='messages')


class InternalNote(db.Model):
    __tablename__ = 'internal_notes'
    id = db.Column(db.Integer, primary_key=True)
    appeal_id = db.Column(db.Integer, db.ForeignKey('appeals.id'), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

    author = db.relationship('User', foreign_keys=[author_id])
    appeal_ref = db.relationship('Appeal', back_populates='internal_notes')


class Attachment(db.Model):
    __tablename__ = 'attachments'
    id = db.Column(db.Integer, primary_key=True)
    appeal_id = db.Column(db.Integer, db.ForeignKey('appeals.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

    appeal_ref = db.relationship('Appeal', back_populates='attachments')


class ActionLog(db.Model):
    __tablename__ = 'action_logs'
    id = db.Column(db.Integer, primary_key=True)
    appeal_id = db.Column(db.Integer, db.ForeignKey('appeals.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    action = db.Column(db.String(200), nullable=False)
    reason = db.Column(db.Text, nullable=True)  # С7.5: Обязательная причина вмешательства
    created_at = db.Column(db.DateTime, default=datetime.now)

    user = db.relationship('User', foreign_keys=[user_id])
    appeal_ref = db.relationship('Appeal', back_populates='logs')