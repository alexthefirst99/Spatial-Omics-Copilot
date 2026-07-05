def setup_work_dir():
    # create a working directory under home directory to store user data
    # the directory is named according to the current time starting with Mjolnir_, following by current time, ending with a random SHA1 hash
    import os
    import datetime
    import uuid
    from app.config import get_path

    date_time = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    base = get_path("paths.workdir_base", os.path.join(project_root, "tmp_data", "workdirs"), env="COPILOT_WORKDIR_BASE")
    os.makedirs(base, exist_ok=True)
    work_dir = os.path.join(base, f"Copilot_{date_time}_{str(uuid.uuid4())}")
    return work_dir
