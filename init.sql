-- 1. СОТРУДНИКИ (их создает админ)
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(100) UNIQUE NOT NULL,
    password VARCHAR(100) NOT NULL,
    name VARCHAR(100) NOT NULL,
    role VARCHAR(20) NOT NULL, -- 'operator', 'expert', 'admin'
    specialization VARCHAR(50), -- 'psychologist', 'lawyer' (для экспертов)
    max_cases INT DEFAULT 5     -- лимит нагрузки
);

-- 2. КАТЕГОРИИ (для С7: админ может добавить новую)
CREATE TABLE categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    target_role VARCHAR(50) NOT NULL -- кому направлять: 'psychologist' или 'lawyer'
);

-- 3. САМИ ОБРАЩЕНИЯ (ядро системы)
CREATE TABLE appeals (
    id SERIAL PRIMARY KEY,
    track_number VARCHAR(20) UNIQUE NOT NULL, -- 'ОТК-4A7B-9X2K'
    applicant_type VARCHAR(20) NOT NULL,     -- 'student', 'parent', 'teacher'
    category_id INT REFERENCES categories(id),
    text TEXT NOT NULL,
    answers JSONB DEFAULT '{}',               -- ответы на 3 вопроса
    
    status VARCHAR(30) DEFAULT 'new',         -- 'new', 'distributed', 'in_progress', 'response_ready', 'returned', 'completed', 'rejected'
    priority VARCHAR(20) DEFAULT 'standard',  -- 'low', 'standard', 'urgent'
    
    is_crisis BOOLEAN DEFAULT FALSE,          -- флаг суицида/насилия (С6)
    emergency_contact VARCHAR(100),           -- телефон/тг если школьник сам оставил
    
    operator_id INT REFERENCES users(id),     -- кто принял
    expert_id INT REFERENCES users(id),       -- кому передали
    
    recommendation TEXT,                      -- финальный совет эксперта
    return_count INT DEFAULT 0,               -- счетчик возвратов "не помогло" (макс 2)
    rating INT,                               -- оценка 1-5
    
    created_at TIMESTAMP DEFAULT NOW()
);

-- 4. ЧАТ (переписка по обращению)
CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    appeal_id INT REFERENCES appeals(id) ON DELETE CASCADE,
    sender VARCHAR(20) NOT NULL, -- 'applicant' или 'expert'
    text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 5. ВНУТРЕННИЕ ЗАМЕТКИ ЭКСПЕРТОВ (заявитель их не видит!)
CREATE TABLE notes (
    id SERIAL PRIMARY KEY,
    appeal_id INT REFERENCES appeals(id) ON DELETE CASCADE,
    author_name VARCHAR(100) NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 6. ЖУРНАЛ ДЛЯ АДМИНА (для сценария С7.5: когда админ вручную пнул зависший тикет)
CREATE TABLE admin_logs (
    id SERIAL PRIMARY KEY,
    appeal_id INT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Сотрудники (пароль у всех: 12345)
INSERT INTO users (email, password, name, role, specialization) VALUES
('operator@otklik.ru', '12345', 'Анна (Оператор)', 'operator', NULL),
('psy@otklik.ru', '12345', 'Иван (Психолог)', 'expert', 'psychologist'),
('law@otklik.ru', '12345', 'Ольга (Юрист)', 'expert', 'lawyer'),
('admin@otklik.ru', '12345', 'Администратор', 'admin', NULL);

-- Базовые категории
INSERT INTO categories (name, target_role) VALUES
('Травля и оскорбления', 'psychologist'),
('Кибербуллинг', 'psychologist'),
('Давление и угрозы', 'lawyer'),
('Конфликт с учителем', 'psychologist');