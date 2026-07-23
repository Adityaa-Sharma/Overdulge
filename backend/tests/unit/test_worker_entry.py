import inspect

from app.main import app as app_instance


def test_worker_entrypoint_imports_the_fastapi_app():
    import worker

    assert worker.app is app_instance


def test_worker_entrypoint_exposes_a_default_class_extending_worker_entrypoint():
    import worker

    assert issubclass(worker.Default, worker.WorkerEntrypoint)


def test_worker_entrypoint_default_class_has_an_async_fetch_method():
    import worker

    assert inspect.iscoroutinefunction(worker.Default.fetch)
