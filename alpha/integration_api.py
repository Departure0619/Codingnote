"""Round-four routes composed with the existing operation/cancellation locks."""
import threading
from fastapi import Query
from . import models
from .bank import Bank, languages
from .store import Missing


def install(app, store, execution, operation_lock, cancellation_lock, cancellations):
    bank = Bank(store)
    app.state.bank = bank

    @app.get('/api/languages')
    def language_registry():
        return languages()

    @app.get('/api/bank')
    def bank_list(q: str = '', primary: str = '', tag: str = '', difficulty: str = '', id: str = ''):
        return bank.listing(q, primary, tag, difficulty, id)

    @app.get('/api/bank/{question_id}')
    def bank_question(question_id: str):
        return bank.public(bank.definition(question_id))

    @app.post('/api/bank/{question_id}/practice')
    def practice(question_id: str, value: models.Practice):
        return bank.practice(question_id, value.language, value.new_task, value.request_id)

    def submit(task_id, value, mode):
        with operation_lock:
            event = threading.Event()
            with cancellation_lock:
                cancellations[(task_id, value.request_id)] = event
            try:
                return bank.judge(task_id, value.version_id, value.request_id, mode, execution, event)
            finally:
                with cancellation_lock:
                    cancellations.pop((task_id, value.request_id), None)

    @app.post('/api/tasks/{task_id}/samples')
    def samples(task_id: str, value: models.Submission):
        return submit(task_id, value, 'samples')

    @app.post('/api/tasks/{task_id}/submit')
    def formal_submit(task_id: str, value: models.Submission):
        return submit(task_id, value, 'formal')

    @app.get('/api/tasks/{task_id}/submissions')
    def submissions(task_id: str):
        store.detail(task_id)
        return bank.submissions(task_id)

    @app.post('/api/tasks/{task_id}/errors')
    def add_error(task_id: str, value: models.ErrorRecord):
        return store.save_error(task_id, value.model_dump(exclude_none=True))

    @app.put('/api/errors/{error_id}')
    def update_error(error_id: str, value: models.ErrorRecord):
        with store.connect() as db:
            record = db.execute('SELECT task_id FROM error_records WHERE id=?', (error_id,)).fetchone()
        if not record:
            raise Missing('错误记录不存在。')
        return store.save_error(record[0], value.model_dump(exclude_none=True), error_id)

    @app.get('/api/associations')
    def associations(task_id: str = ''):
        return store.associations(task_id)

    @app.get('/api/associations/{association_id}')
    def association(association_id: str):
        return store.association(association_id)

    @app.post('/api/associations')
    def create_association(value: models.Association):
        return store.create_association(value.left_error_id, value.right_error_id, value.rationale)

    @app.patch('/api/associations/{association_id}')
    def decide_association(association_id: str, value: models.AssociationStatus):
        return store.decide_association(association_id, value.status)

    @app.get('/api/preferences')
    def preferences(user_id: str = Query('local', min_length=1, max_length=80), task_id: str | None = None):
        return store.preferences(user_id, task_id)

    @app.put('/api/preferences')
    def save_preferences(value: models.Preferences, user_id: str = Query('local', min_length=1, max_length=80), task_id: str | None = None):
        return store.preferences(user_id, task_id, value.model_dump(exclude_none=True))

    @app.post('/api/preferences/reset')
    def reset_preferences(user_id: str = Query('local', min_length=1, max_length=80), task_id: str | None = None):
        return store.preferences(user_id, task_id, reset=True)

    return bank
