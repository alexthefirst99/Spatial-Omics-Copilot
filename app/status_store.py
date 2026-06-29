import json
import os

# Local file-based status store
STATUS_DIR = os.environ.get(
    'COPILOT_STATUS_DIR',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'status_data')
)
os.makedirs(STATUS_DIR, exist_ok=True)


def _status_file(job_id):
    return os.path.join(STATUS_DIR, f"{job_id}.json")


def update_status(job_id, progress, message):
    try:
        data = {"progress": int(progress), "message": message}
        path = _status_file(job_id)
        tmp_path = path + '.tmp'
        with open(tmp_path, 'w') as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
    except Exception as e:
        print(f"[status_store] Failed to update status for {job_id}: {e}")


def get_status(job_id):
    try:
        path = _status_file(job_id)
        if not os.path.exists(path):
            return {"progress": 0, "message": "Waiting for server..."}
        with open(path) as f:
            status = json.load(f)
        if "progress" in status:
            status["progress"] = int(status["progress"])
        if status.get("progress") == 100:
            try:
                os.remove(path)
            except Exception:
                pass
        return status
    except Exception:
        return {"progress": 0, "message": "Waiting for server..."}
