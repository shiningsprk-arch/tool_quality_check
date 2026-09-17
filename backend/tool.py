# -*- coding: utf-8 -*-
"""质量体检：MyBooks 外部工具（Toolbox 工具包）后端。

manifest.json 的 `entry_backend` 指向 `QualityCheckTool`，`api_routes` 指向本模块的
若干 Handler，宿主 toolbox_manager 把它们挂到：

    GET  /api/toolbox/tool/quality_check/checks    检查项清单
    GET  /api/toolbox/tool/quality_check/books     选书搜索
    POST /api/toolbox/tool/quality_check/start     启动体检
    GET  /api/toolbox/tool/quality_check/progress  任务进度
    GET  /api/toolbox/tool/quality_check/report    体检报告（分页/筛选）
    GET  /api/toolbox/tool/quality_check/summary   体检报告（按检查项汇总）
    POST /api/toolbox/tool/quality_check/cancel    取消任务

两个外部工具特有的注意点：

1. 宿主挂 `api_routes` 时只额外包一层"工具被禁用就 404"的 `prepare()`，**不注入任何
   鉴权装饰器**（内置工具的路由是在 handlers/toolbox.py 里手写、自带 `@js @is_admin`）。
   所以这里必须显式写 `@js @is_admin`。
2. `api_routes[].path` 是正则片段，前缀 `/api/toolbox/tool/<tool_id>/` 由宿主拼。

检查逻辑一行没改：`qc/` 是上游 Quality Check（GPL-3, Grant Drake）的检查模块，只改了
import；`qc/shim/` 用最小实现补上它需要的 calibre API；`adapter.py` 把宿主 CoreAPI 伪装
成它期望的 `db`/`gui`；`driver.py` 负责范围、进度与报告。**本工具只读**，不写书库。

报告不放进 `progress_data`：整库体检结果是 MB 级，而 `progress_data` 会进后台任务面板和
每次 `/progress` 响应。因此只把计数放进度里，完整报告写进工作目录 `report.json`，由
`/report` 分页读取。
"""
import json
import logging
import os
import shutil
import threading
import time
from typing import Optional

from webserver.handlers.base import BaseHandler, is_admin, js
from webserver.i18n import _
from webserver.services import AsyncService
from webserver.services.background_service import BackgroundService, BackgroundTask
from webserver.toolbox.base_tool import BaseTool

from . import driver
from .qc import menus

REPORT_FILENAME = 'report.json'

# 单次体检最多覆盖的书本数：整库体检是逐检查逐书跑，上限防止误点全库把服务拖住。
MAX_BOOKS = 5000
# 检查项上限（上游一共 77 个）
MAX_CHECKS = 200

_STATUS_RUNNING = BackgroundTask.STATUS_RUNNING
_STATUS_COMPLETED = BackgroundTask.STATUS_COMPLETED
_STATUS_FAILED = BackgroundTask.STATUS_FAILED


class QualityCheckTool(BaseTool):
    """语言无关的书库/EPUB 质量体检（上游 Quality Check 的只读移植）。"""

    service_item_name = '质量体检'

    # 同一时刻只允许一个体检任务。`_accepted` 覆盖"已受理、但后台线程还没开跑"这段窗口：
    # 只看任务状态的话，两次几乎同时到达的 /start 都能通过，而且取消会打到旧的事件上。
    _state_lock = threading.Lock()
    _accepted = False
    _last_task_id: Optional[int] = None
    _cancel_event = threading.Event()

    # 与仓库根 manifest.json 的对应字段保持一致
    @staticmethod
    def info() -> dict:
        return {
            'tool_id': 'quality_check',
            'name': '质量体检',
            'description': '书库与 EPUB 质量体检：结构、元数据、封面、排版共 75 项检查，只读不改书',
            'revision': '1.1.0',
            'author': '黏菌',
            'publish_date': '2026-09-17',
            'repo_url': 'https://github.com/shiningsprk-arch/tool_quality_check',
        }

    # ---------------------------------------------------------------- 任务状态

    @classmethod
    def _task_status(cls) -> str:
        """(不加锁) 最近一次任务的状态，没有任务时返回空串。"""
        if cls._last_task_id is None:
            return ''
        try:
            task = BackgroundService().get_task(cls._last_task_id)
        except Exception:
            return ''
        return (task or {}).get('status') or ''

    @classmethod
    def get_last_task(cls) -> Optional[dict]:
        if cls._last_task_id is None:
            return None
        return BackgroundService().get_task(cls._last_task_id)

    @classmethod
    def is_running(cls) -> bool:
        """已受理或正在运行（含已受理但后台线程尚未开跑的窗口）。"""
        with cls._state_lock:
            accepted = cls._accepted
        return accepted or cls._task_status() == _STATUS_RUNNING

    @classmethod
    def begin_task(cls) -> Optional[threading.Event]:
        """原子地占位并返回本次任务的取消事件；已有任务在跑时返回 None。

        `/start` 在调用 `run()` 之前调用它：占位成功后，排在后台队列里的这段时间内到达的
        取消请求也会打到同一个事件上，不会被 `run()` 里的初始化覆盖掉。
        """
        with cls._state_lock:
            if cls._accepted or cls._task_status() == _STATUS_RUNNING:
                return None
            cls._accepted = True
            cls._cancel_event = threading.Event()
            return cls._cancel_event

    @classmethod
    def finish_task(cls) -> None:
        with cls._state_lock:
            cls._accepted = False

    @classmethod
    def request_cancel(cls) -> bool:
        """请求取消当前任务；没有可取消的任务时返回 False。"""
        with cls._state_lock:
            if not (cls._accepted or cls._task_status() == _STATUS_RUNNING):
                return False
            cls._cancel_event.set()
            return True

    # ---------------------------------------------------------------- 工作目录

    def report_path(self, task_id: int) -> str:
        return os.path.join(self.api.storage.get_work_dir(str(task_id)), REPORT_FILENAME)

    def cover_root(self, task_id: int) -> str:
        """封面检查按 `<library_path>/<book path>/cover.jpg` 取图，这里给出落盘目录。"""
        return os.path.join(self.api.storage.get_work_dir(str(task_id)), 'covers')

    # ---------------------------------------------------------------- 启动体检

    # ---------------------------------------------------------------- 范围解析

    @staticmethod
    def _scope_ids(api, scope: str, book_ids=None, query: str = '') -> list:
        """把请求里的范围解析成 book_id 列表（纯逻辑，便于单测）。"""
        if scope == 'all':
            return list(api.calibre.all_book_ids())
        if scope == 'query':
            query = (query or '').strip()
            if not query:
                return []
            return list(api.calibre.search_ids(query))
        return sorted(set(int(i) for i in (book_ids or [])))

    @AsyncService.register_function
    def resolve_scope(self, scope: str, book_ids=None, query: str = '') -> list:
        """解析范围。用 `register_function` 是因为要**同步拿返回值**给 handler。"""
        return self._scope_ids(self.api, scope, book_ids, query)

    @AsyncService.register_function
    def search_books(self, query: str, limit: int = 30) -> list:
        """选书器搜索：无查询词时回一批书，便于直接勾选。"""
        if query:
            pattern = query if ':' in query else 'title:%s' % query
            raw = self.api.calibre.search_books(pattern, max_results=limit) or []
        else:
            ids = self.api.calibre.all_book_ids()[:limit]
            raw = self.api.calibre.get_data_as_dict(ids) if ids else []
        books = []
        for book in raw:
            if not isinstance(book, dict):
                continue
            books.append({
                'id': book.get('id'),
                'title': book.get('title') or '',
                'author': book.get('author') or '',
                'thumb': book.get('thumb') or '',
                'formats': book.get('available_formats') or '',
            })
        return books

    @AsyncService.register_service
    def run(self, user_id: int, scope: str, book_ids, check_keys, options, scope_label: str = '',
            cancel_event=None):
        """后台执行体检。`register_service` 恒异步：调用方拿不到返回值，进度走 `api.tasks`。

        `cancel_event` 由 `/start` 的 handler 在 `begin_task()` 里占位时创建后传进来；这里
        **不能**自己新建事件，否则"受理"到"后台线程真正开跑"之间到达的取消会被丢掉。
        """
        cancel_event = cancel_event or threading.Event()
        task_id = self.create_task({
            'status': 'running',
            'stage': 'resolving',
            'scope': scope,
            'scope_label': scope_label,
            'check_index': 0,
            'check_total': len(check_keys or []),
            'check_key': '',
            'done': 0,
            'total': 0,
            'current_id': 0,
            'current_title': '',
            'issues_total': 0,
            'severity_counts': {'error': 0, 'warn': 0, 'info': 0},
            'errors_count': 0,
        })
        QualityCheckTool._last_task_id = task_id

        try:
            ids = self._scope_ids(self.api, scope, book_ids)
            ids = ids[:MAX_BOOKS]
            check_keys = [k for k in (check_keys or []) if k in menus.PLUGIN_MENUS][:MAX_CHECKS]
            if not ids or not check_keys:
                self.complete_task(task_id, error_message=_('没有可体检的书或检查项'))
                return

            cover_root = self.cover_root(task_id)
            os.makedirs(cover_root, exist_ok=True)

            state = {'issues_total': 0}
            last_push = {'t': 0.0}

            def progress_cb(check_index, check_total, check_key, done, total, book_id, title):
                # 进度按"检查项"为总刻度，检查项内部再按书推进。逐书写任务进度会在整库体检时
                # 变成几十万次写入，所以按 0.7s 节流，但每个检查项的首尾一定推一次。
                now = time.time()
                if done not in (1, total) and (now - last_push['t']) < 0.7:
                    return
                last_push['t'] = now
                percent = int(((check_index + (done / float(total or 1))) / max(check_total, 1)) * 100)
                self.update_task_progress(task_id, max(0, min(percent, 99)), {
                    'status': 'running',
                    'stage': 'checking',
                    'check_index': check_index + 1,
                    'check_total': check_total,
                    'check_key': check_key,
                    'check_name': (menus.PLUGIN_MENUS.get(check_key) or {}).get('name', check_key),
                    'done': done,
                    'total': total,
                    'current_id': book_id,
                    'current_title': title or '',
                    'issues_total': state['issues_total'],
                    'errors_count': 0,
                })

            report = driver.run_checks(
                api=self.api,
                book_ids=ids,
                check_keys=check_keys,
                options=options or {},
                progress_cb=progress_cb,
                cancel_event=cancel_event,
                cover_root=cover_root,
            )
            report['task_id'] = task_id
            report['scope_type'] = scope
            report['scope_label'] = scope_label
            report['generated_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            report['options'] = options or {}

            with open(self.report_path(task_id), 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False)

            counts = report.get('severity_counts') or {}
            self.update_task_progress(task_id, 100, {
                'status': 'done',
                'stage': 'done',
                'check_total': len(check_keys),
                'check_index': len(check_keys),
                'done': len(ids),
                'total': len(ids),
                'issues_total': report.get('issues_total', 0),
                'severity_counts': counts,
                'errors_count': len(report.get('errors') or []),
                'books_with_issues': report.get('books_with_issues', 0),
                'cancelled': report.get('cancelled', False),
            })
            self.complete_task(task_id)
            summary = _('体检完成：%d 本有问题的书 / 共 %d 本，%d 条问题') % (
                report.get('books_with_issues', 0), len(ids), report.get('issues_total', 0))
            self.add_msg(user_id, 'success', summary)
        except Exception as e:
            logging.exception('[QualityCheckTool] 体检失败')
            self.complete_task(task_id, error_message='%s: %s' % (type(e).__name__, e))
        finally:
            # 封面是按需物化到工作目录的，跑完即弃；报告本体保留给 /report 读取。
            shutil.rmtree(self.cover_root(task_id), ignore_errors=True)
            QualityCheckTool.finish_task()


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

class ChecksHandler(BaseHandler):
    """GET /api/toolbox/tool/quality_check/checks —— 检查项清单（供前端勾选）。"""

    @js
    @is_admin
    def get(self):
        return {'err': 'ok', 'data': {'checks': driver.describe_checks()}}


class BooksHandler(BaseHandler):
    """GET /api/toolbox/tool/quality_check/books?q=... —— 选书器搜索。"""

    @js
    @is_admin
    def get(self):
        query = (self.get_argument('q', '') or '').strip()
        limit = max(1, min(int(self.get_argument('limit', '30') or 30), 60))
        books = QualityCheckTool().search_books(query, limit)
        return {'err': 'ok', 'data': {'books': books}}


class StartHandler(BaseHandler):
    """POST /api/toolbox/tool/quality_check/start —— 启动体检（立即返回，进度走 /progress）。

    请求体（JSON）::

        {"scope": "all"|"picked"|"query"|"single",
         "book_ids": [int], "query": str, "checks": [str],
         "options": {"qc": {...}, "cover": {...}}}

    `scope=all` 表示全库；`picked`/`single` 用 `book_ids`；`query` 用 Calibre 查询串。
    同一时刻只允许一个体检任务在跑（占位与启动之间没有缝隙，见 `begin_task`）。
    """

    @js
    @is_admin
    async def post(self):
        # 快速拒绝：省掉下面那次范围解析。真正的互斥判定在 begin_task()。
        if QualityCheckTool.is_running():
            return {'err': 'task.running', 'msg': _('已有体检任务在运行')}

        try:
            payload = json.loads(self.request.body.decode('utf-8') or '{}')
        except (ValueError, UnicodeDecodeError):
            return {'err': 'params.invalid', 'msg': _('请求体不是合法 JSON')}
        if not isinstance(payload, dict):
            return {'err': 'params.invalid', 'msg': _('请求体格式错误')}

        scope = payload.get('scope') or 'single'
        if scope not in ('all', 'picked', 'single', 'query'):
            return {'err': 'params.invalid', 'msg': _('不支持的范围类型')}

        book_ids = payload.get('book_ids') or []
        if not isinstance(book_ids, list) or any(not isinstance(i, int) for i in book_ids):
            return {'err': 'params.invalid', 'msg': _('book_ids 必须是整数数组')}
        if scope != 'all' and not book_ids and scope != 'query':
            return {'err': 'scope.empty', 'msg': _('没有选择书籍')}

        query = payload.get('query') or ''
        if scope == 'query' and not query.strip():
            return {'err': 'scope.empty', 'msg': _('没有填写查询条件')}

        checks = payload.get('checks') or []
        if not isinstance(checks, list):
            return {'err': 'params.invalid', 'msg': _('checks 必须是字符串数组')}
        checks = [str(c) for c in checks if str(c) in menus.PLUGIN_MENUS]
        if not checks:
            return {'err': 'checks.empty', 'msg': _('没有选择检查项')}

        options = payload.get('options') or {}
        if not isinstance(options, dict):
            options = {}

        tool = QualityCheckTool()
        try:
            ids = tool.resolve_scope(scope, book_ids, query)
        except Exception as e:
            return {'err': 'scope.invalid', 'msg': _('查询条件无法解析：%s') % e}
        if not ids:
            return {'err': 'scope.empty', 'msg': _('这个范围里没有书')}
        if len(ids) > MAX_BOOKS:
            return {'err': 'scope.too_large',
                    'msg': _('一次最多体检 %d 本，当前 %d 本') % (MAX_BOOKS, len(ids))}

        # 到这一步为止都还没有副作用，所以占位失败可以干净地返回。
        cancel_event = QualityCheckTool.begin_task()
        if cancel_event is None:
            return {'err': 'task.running', 'msg': _('已有体检任务在运行')}

        label = {'all': _('全库'), 'picked': _('已选 %d 本') % len(ids),
                 'single': _('单本'), 'query': _('查询：%s') % query}[scope]
        tool.run(self.user_id(), scope, ids, checks, options, label, cancel_event)
        return {'err': 'ok', 'msg': _('体检已启动'), 'data': {'total': len(ids), 'checks': len(checks)}}


class ProgressHandler(BaseHandler):
    """GET /api/toolbox/tool/quality_check/progress —— 任务进度（不含报告正文）。"""

    @js
    @is_admin
    def get(self):
        task = QualityCheckTool.get_last_task()
        if not task:
            return {'err': 'task.not_found', 'msg': _('尚未启动体检任务')}
        data = task.get('progress_data') or {}
        result = {
            'status': task.get('status'),
            'progress': task.get('progress', 0),
            'stage': data.get('stage', ''),
            'scope_label': data.get('scope_label', ''),
            'check_index': data.get('check_index', 0),
            'check_total': data.get('check_total', 0),
            'check_key': data.get('check_key', ''),
            'check_name': data.get('check_name', ''),
            'done': data.get('done', 0),
            'total': data.get('total', 0),
            'current_id': data.get('current_id', 0),
            'current_title': data.get('current_title', ''),
            'issues_total': data.get('issues_total', 0),
            'errors_count': data.get('errors_count', 0),
            'books_with_issues': data.get('books_with_issues', 0),
            'severity_counts': data.get('severity_counts') or {},
            'cancelled': data.get('cancelled', False),
        }
        if task.get('status') == _STATUS_FAILED:
            return {'err': 'task.failed', 'msg': task.get('error_message') or _('体检失败'), 'data': result}
        if task.get('status') == _STATUS_COMPLETED:
            return {'err': 'ok', 'msg': _('体检已完成'), 'data': result}
        return {'err': 'ok', 'data': result}


def _report_task_id(argument: str) -> Optional[int]:
    """把 `task_id` 参数（缺省时用最近一次任务）解析成 int，非法返回 None。"""
    task_id = argument or str(QualityCheckTool._last_task_id or '')
    if not task_id or not task_id.isdigit():
        return None
    return int(task_id)


def _read_report(task_id: int):
    """读回任务工作目录里的报告文件，返回 ``(report, error_response)``。"""
    tool = QualityCheckTool()
    path = tool.report_path(task_id)
    if not os.path.exists(path):
        return None, {'err': 'report.not_ready', 'msg': _('报告还没生成或已被清理')}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f), None
    except (OSError, ValueError) as e:
        return None, {'err': 'report.invalid', 'msg': _('报告读取失败：%s') % e}


class ReportHandler(BaseHandler):
    """GET /api/toolbox/tool/quality_check/report —— 分页读取体检报告。

    参数：`task_id`（可选，默认最近一次）、`offset`、`limit`、`severity`、`check`。
    报告文件在任务工作目录里，整库体检可能几 MB，所以服务端分页；`severity`/`check`
    筛选同样在服务端做，并用 `filtered_total`/`filtered_issues` 回传筛选后的计数（报表
    里的 `total_books`/`issues_total` 始终是全量口径）。
    """

    @js
    @is_admin
    def get(self):
        task_id = _report_task_id(self.get_argument('task_id', ''))
        if task_id is None:
            return {'err': 'task.not_found', 'msg': _('尚未启动体检任务')}

        report, error = _read_report(task_id)
        if error is not None:
            return error

        severity = (self.get_argument('severity', '') or '').strip()
        check_key = (self.get_argument('check', '') or '').strip()

        books, filtered_issues = driver.filter_report_books(report.get('books') or [],
                                                            severity, check_key)
        total = len(books)
        offset = max(0, int(self.get_argument('offset', '0') or 0))
        limit = max(1, min(int(self.get_argument('limit', '100') or 100), 500))

        return {'err': 'ok', 'data': {
            'task_id': task_id,
            'generated_at': report.get('generated_at', ''),
            'scope_type': report.get('scope_type', ''),
            'scope_label': report.get('scope_label', ''),
            'total_books': report.get('total_books', 0),
            'books_with_issues': report.get('books_with_issues', 0),
            'issues_total': report.get('issues_total', 0),
            'severity_counts': report.get('severity_counts') or {},
            'summary': report.get('summary') or {},
            'per_check': report.get('per_check') or {},
            'notes': report.get('notes') or {},
            'skipped': report.get('skipped') or {},
            'errors': (report.get('errors') or [])[:100],
            'cancelled': report.get('cancelled', False),
            'filtered_total': total,
            'filtered_issues': filtered_issues,
            'offset': offset,
            'books': books[offset:offset + limit],
        }}


class SummaryHandler(BaseHandler):
    """GET /api/toolbox/tool/quality_check/summary —— 报告的"按检查项"汇总。

    与 `/report` 读同一份 report.json，但只回聚合结果：每个检查项命中多少本书、带多少条
    明细、以及前几本书（供链接）。整库体检最典型的用法是"先看哪类问题最多，再决定修哪批
    书"，这个视图就为它准备，一次请求就能渲染。
    """

    @js
    @is_admin
    def get(self):
        task_id = _report_task_id(self.get_argument('task_id', ''))
        if task_id is None:
            return {'err': 'task.not_found', 'msg': _('尚未启动体检任务')}

        report, error = _read_report(task_id)
        if error is not None:
            return error

        data = driver.summarize_report(report)
        data.update({
            'task_id': task_id,
            'generated_at': report.get('generated_at', ''),
            'scope_type': report.get('scope_type', ''),
            'scope_label': report.get('scope_label', ''),
            'notes': report.get('notes') or {},
            'skipped': report.get('skipped') or {},
            'errors_count': len(report.get('errors') or []),
            'cancelled': report.get('cancelled', False),
        })
        return {'err': 'ok', 'data': data}


class CancelHandler(BaseHandler):
    """POST /api/toolbox/tool/quality_check/cancel —— 请求取消（循环在下一个检查项边界停下）。"""

    @js
    @is_admin
    def post(self):
        if not QualityCheckTool.request_cancel():
            return {'err': 'task.not_found', 'msg': _('没有正在运行的体检任务')}
        return {'err': 'ok', 'msg': _('已请求取消，当前检查项跑完即停')}
