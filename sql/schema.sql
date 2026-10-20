-- 每日 AI 新闻助手。SQLite，由应用启动时执行。

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    legacy_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preferences (
    user_id TEXT PRIMARY KEY,
    topics TEXT NOT NULL DEFAULT '[]',
    keywords TEXT NOT NULL DEFAULT '[]',
    language TEXT NOT NULL DEFAULT 'zh',
    recall_count INTEGER NOT NULL DEFAULT 5,
    custom_prompt TEXT NOT NULL DEFAULT '',
    preferred_sites TEXT NOT NULL DEFAULT '[]',
    site_hits TEXT NOT NULL DEFAULT '{}',
    search_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT,
    query TEXT,
    items TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_briefings_user_created
    ON briefings (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    briefing_id INTEGER NOT NULL,
    step_index INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    arguments TEXT NOT NULL,
    result TEXT NOT NULL,
    ok INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (briefing_id) REFERENCES briefings(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_steps_briefing
    ON agent_steps (briefing_id, step_index);
