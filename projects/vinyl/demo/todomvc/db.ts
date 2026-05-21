/**
 * TodoMVC demo — persistence. State lives 100% in sqlite (better-sqlite3);
 * there is no in-memory or client-side state. The server renders from these
 * reads, and actions are the only writers. This is the "DB is the source of
 * truth" locked decision made concrete.
 *
 * The library is bring-your-own-db; this module is demo code, not part of the
 * published surface.
 */
import Database from "better-sqlite3";

export type DB = Database.Database;

export interface Todo {
  id: number;
  text: string;
  done: boolean;
}

export function openTodoDb(path = ":memory:"): DB {
  const db = new Database(path);
  db.exec(`
    CREATE TABLE IF NOT EXISTS todos (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      text TEXT NOT NULL,
      done INTEGER NOT NULL DEFAULT 0
    )
  `);
  return db;
}

export function listTodos(db: DB): Todo[] {
  const rows = db
    .prepare("SELECT id, text, done FROM todos ORDER BY id")
    .all() as Array<{ id: number; text: string; done: number }>;
  return rows.map((row) => ({
    id: row.id,
    text: row.text,
    done: row.done === 1,
  }));
}

export function addTodo(db: DB, text: string): void {
  db.prepare("INSERT INTO todos (text) VALUES (?)").run(text);
}

export function toggleTodo(db: DB, id: number): void {
  db.prepare("UPDATE todos SET done = 1 - done WHERE id = ?").run(id);
}

export function clearCompleted(db: DB): void {
  db.prepare("DELETE FROM todos WHERE done = 1").run();
}

export function activeCount(db: DB): number {
  const row = db
    .prepare("SELECT COUNT(*) AS n FROM todos WHERE done = 0")
    .get() as { n: number };
  return row.n;
}
