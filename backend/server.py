"""Local demonstration API and static server. Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sqlite3
import time
import uuid
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
import mimetypes

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'project.json').read_text(encoding='utf-8'))
DB_PATH = Path(os.environ.get('DEMO_DB', str(ROOT / 'backend' / 'data' / 'demo.sqlite3')))


def connect():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def audit(db, action, detail):
    db.execute('INSERT INTO audit(action,detail,created_at) VALUES(?,?,?)', (action, detail, now()))


def initialize():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, cells TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, payload TEXT NOT NULL, stock INTEGER NOT NULL CHECK(stock>=0));
        CREATE TABLE IF NOT EXISTS submissions(id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id), name TEXT NOT NULL,
          quantity INTEGER NOT NULL CHECK(quantity>0), start_date TEXT, end_date TEXT, note TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT '已登记', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        if not db.execute("SELECT 1 FROM metadata WHERE key='seeded'").fetchone():
            for row in CONFIG['records']:
                db.execute('INSERT INTO records VALUES(?,?,?,?)', (row['id'], json.dumps(row['cells'], ensure_ascii=False), row['status'], now()))
            for item in CONFIG['items']:
                db.execute('INSERT INTO items VALUES(?,?,?)', (item['id'], json.dumps(item, ensure_ascii=False), item['stock']))
            db.execute("INSERT INTO metadata VALUES('seeded','1')")
            audit(db, '初始化', '已载入本项目的示例业务数据')


def record_dict(row):
    return dict(id=row['id'], cells=json.loads(row['cells']), status=row['status'], updated_at=row['updated_at'])


def item_dict(row):
    return {**json.loads(row['payload']), 'stock': row['stock']}


def text_field(body, key, limit=200, required=True):
    value = body.get(key, '')
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        raise ValueError(f'{key} 必须是 1–{limit} 字的文本' if required else f'{key} 最多 {limit} 字')
    return value.strip()


class Handler(BaseHTTPRequestHandler):
    server_version = 'ProjectDemo/1.0'

    def send(self, value, status=200, content_type='application/json; charset=utf-8'):
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store' if self.path.startswith('/api/') else 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size < 0 or size > 65536:
            raise ValueError('请求体不能超过 64KB')
        try:
            body = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeError):
            raise ValueError('请求体必须是合法 JSON')
        if not isinstance(body, dict):
            raise ValueError('请求体必须是 JSON 对象')
        return body

    def do_GET(self):
        try:
            self.get()
        except (ValueError, TypeError):
            self.send({'error': '查询参数不合法'}, 400)
        except sqlite3.Error:
            self.send({'error': '数据库暂不可用'}, 500)

    def get(self):
        url = urlsplit(self.path)
        path = unquote(url.path)
        query = parse_qs(url.query)
        with connect() as db:
            if path == '/api/health':
                return self.send({'status': 'ok', 'project': CONFIG['title'], 'storage': 'SQLite'})
            if path == '/api/records':
                rows = [record_dict(r) for r in db.execute('SELECT * FROM records ORDER BY rowid DESC')]
                keyword = query.get('q', [''])[0].casefold()
                status = query.get('status', [''])[0]
                rows = [r for r in rows if keyword in ' '.join(r['cells']).casefold() and (not status or r['status'] == status)]
                page = max(1, int(query.get('page', ['1'])[0]))
                size = min(100, max(1, int(query.get('page_size', ['20'])[0])))
                return self.send({'items': rows[(page-1)*size:page*size], 'total': len(rows), 'page': page, 'page_size': size})
            if path == '/api/items':
                items = [item_dict(r) for r in db.execute('SELECT * FROM items ORDER BY rowid')]
                keyword, category = query.get('q', [''])[0].casefold(), query.get('category', [''])[0]
                return self.send({'items': [x for x in items if keyword in (x['title']+' '+x['place']).casefold() and (not category or category == x['cat'])]})
            if path == '/api/submissions':
                rows = [dict(r) for r in db.execute('SELECT s.*,i.payload FROM submissions s JOIN items i ON i.id=s.item_id ORDER BY s.rowid DESC')]
                for row in rows:
                    row['title'] = json.loads(row.pop('payload'))['title']
                return self.send({'items': rows})
            if path == '/api/audit':
                return self.send({'items': [dict(r) for r in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 100')]})
            if path == '/api/stats':
                states = {r['status']: r['n'] for r in db.execute('SELECT status,COUNT(*) n FROM records GROUP BY status')}
                return self.send({'records': sum(states.values()), 'states': states,
                                  'items': db.execute('SELECT COUNT(*) FROM items').fetchone()[0],
                                  'submissions': db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0]})
            if path == '/api/runs':
                return self.send({'items': [dict(id=r['id'], **json.loads(r['payload']), created_at=r['created_at']) for r in db.execute('SELECT * FROM runs ORDER BY rowid DESC LIMIT 20')]})
            if path == '/api/export':
                out = io.StringIO()
                writer = csv.writer(out)
                writer.writerow(CONFIG['columns'] + ['状态'])
                for r in db.execute('SELECT * FROM records ORDER BY rowid DESC'):
                    cells = json.loads(r['cells']) + [CONFIG['statuses'].get(r['status'], r['status'])]
                    writer.writerow(["'"+value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value for value in cells])
                return self.send(('\ufeff'+out.getvalue()).encode(), content_type='text/csv; charset=utf-8')
            if path == '/metrics':
                count = db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
                count2 = db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0]
                metrics = f'# HELP demo_records Number of persisted demo records.\n# TYPE demo_records gauge\ndemo_records {count}\n# HELP demo_submissions Number of persisted demo submissions.\n# TYPE demo_submissions gauge\ndemo_submissions {count2}\n'
                return self.send(metrics.encode(), content_type='text/plain; version=0.0.4; charset=utf-8')
        if path.startswith('/api/'):
            return self.send({'error': '接口不存在'}, 404)
        # An explicit static allowlist prevents exposing the database or backend source.
        if path in ('/', '/index.html'):
            target = ROOT / 'index.html'
        elif path in ('/preview.png', '/preview-full.png', '/favicon.svg') or path.startswith('/assets/'):
            target = (ROOT / path.lstrip('/')).resolve()
            if ROOT not in target.parents or not target.is_file() or (path.startswith('/assets/') and not target.is_relative_to(ROOT / 'assets')):
                return self.send({'error': '文件不存在'}, 404)
        else:
            return self.send({'error': '文件不存在'}, 404)
        mime = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        self.send(target.read_bytes(), content_type=mime + ('; charset=utf-8' if mime.startswith('text/') or mime == 'application/javascript' else ''))

    def do_POST(self):
        self.mutate('POST')

    def do_PATCH(self):
        self.mutate('PATCH')

    def do_DELETE(self):
        self.mutate('DELETE')

    def mutate(self, method):
        try:
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + self.headers.get('Host', ''):
                return self.send({'error': '请从本项目页面提交'}, 403)
            path = urlsplit(self.path).path
            body = {} if method == 'DELETE' else self.body()
            with connect() as db:
                if path == '/api/records' and method == 'POST' or path.startswith('/api/records/') and method == 'PATCH':
                    cells = body.get('cells')
                    if not isinstance(cells, list) or len(cells) != len(CONFIG['columns']) or any(not isinstance(x, str) or not x.strip() or len(x) > 500 for x in cells):
                        raise ValueError('请填写完整字段，每个字段不超过 500 字')
                    status = body.get('status', next(iter(CONFIG['statuses'])))
                    if status not in CONFIG['statuses']:
                        raise ValueError('状态无效')
                    cells = [x.strip() for x in cells]
                    if method == 'POST':
                        rid = uuid.uuid4().hex[:12]
                        db.execute('INSERT INTO records VALUES(?,?,?,?)', (rid, json.dumps(cells, ensure_ascii=False), status, now()))
                    else:
                        rid = path.rsplit('/', 1)[-1]
                        if not db.execute('UPDATE records SET cells=?,status=?,updated_at=? WHERE id=?', (json.dumps(cells, ensure_ascii=False), status, now(), rid)).rowcount:
                            return self.send({'error': '记录不存在'}, 404)
                    audit(db, '新增记录' if method == 'POST' else '更新记录', cells[0])
                    return self.send({'id': rid, 'message': '已保存'}, 201 if method == 'POST' else 200)
                if path.startswith('/api/records/') and method == 'DELETE':
                    rid = path.rsplit('/', 1)[-1]
                    if not db.execute('DELETE FROM records WHERE id=?', (rid,)).rowcount:
                        return self.send({'error': '记录不存在'}, 404)
                    audit(db, '删除记录', rid)
                    return self.send({'message': '已删除'})
                if path == '/api/submissions' and method == 'POST':
                    name, item_id = text_field(body, 'name', 40), text_field(body, 'item_id', 40)
                    note = text_field(body, 'note', 500, False)
                    qty = body.get('quantity', 1)
                    if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= 100:
                        raise ValueError('数量必须是 1–100 的整数')
                    start, end = '', ''
                    if CONFIG['dated']:
                        start, end = text_field(body, 'start_date', 10), text_field(body, 'end_date', 10)
                        if date.fromisoformat(start) < date.today() or date.fromisoformat(end) <= date.fromisoformat(start):
                            raise ValueError('开始日期不能早于今天，结束日期必须晚于开始日期')
                    # Serialize the availability check and reservation in one write transaction.
                    db.execute('BEGIN IMMEDIATE')
                    row = db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
                    if row is None:
                        return self.send({'error': '资源不存在'}, 404)
                    if CONFIG['dated']:
                        used = db.execute("SELECT COALESCE(SUM(quantity),0) FROM submissions WHERE item_id=? AND status!='已取消' AND start_date<? AND end_date>?", (item_id, end, start)).fetchone()[0]
                        if row['stock'] - used < qty:
                            return self.send({'error': '该日期范围余量不足，请调整日期或数量'}, 409)
                    elif not db.execute('UPDATE items SET stock=stock-? WHERE id=? AND stock>=?', (qty, item_id, qty)).rowcount:
                        return self.send({'error': '剩余名额或库存不足'}, 409)
                    sid = 'D' + uuid.uuid4().hex[:10].upper()
                    db.execute('INSERT INTO submissions(id,item_id,name,quantity,start_date,end_date,note,created_at) VALUES(?,?,?,?,?,?,?,?)', (sid,item_id,name,qty,start,end,note,now()))
                    audit(db, CONFIG['action'], json.loads(row['payload'])['title'] + ' · ' + sid)
                    return self.send({'id': sid, 'message': '已登记，记录已保存到本地数据库'}, 201)
                if path == '/api/run' and method == 'POST' and CONFIG['layout'] == 'testing':
                    began = time.perf_counter()
                    checks = [
                        ('数据库连通性', db.execute('SELECT 1').fetchone()[0] == 1),
                        ('业务记录字段完整性', all(len(json.loads(r['cells'])) == len(CONFIG['columns']) for r in db.execute('SELECT cells FROM records'))),
                        ('业务状态合法性', all(r['status'] in CONFIG['statuses'] for r in db.execute('SELECT status FROM records'))),
                        ('库存非负约束', db.execute('SELECT COUNT(*) FROM items WHERE stock<0').fetchone()[0] == 0),
                    ]
                    run = dict(mode='本地数据自检', checks=[{'name': n, 'passed': bool(p)} for n,p in checks], duration_ms=round((time.perf_counter()-began)*1000, 2))
                    rid = uuid.uuid4().hex[:12]
                    db.execute('INSERT INTO runs VALUES(?,?,?)', (rid,json.dumps(run, ensure_ascii=False),now()))
                    audit(db, '执行自检', f'{sum(p for _,p in checks)}/{len(checks)} 通过；未调用外部测试工具')
                    return self.send({'id': rid, **run}, 201)
                return self.send({'error': '接口不存在'}, 404)
        except (ValueError, TypeError) as exc:
            self.send({'error': str(exc)}, 400)
        except sqlite3.IntegrityError:
            self.send({'error': '数据约束冲突'}, 409)
        except sqlite3.Error:
            self.send({'error': '数据库暂不可用'}, 500)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=CONFIG['title'])
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    initialize()
    print(f"{CONFIG['title']}\nhttp://{args.host}:{args.port}", flush=True)
    try:
        ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
