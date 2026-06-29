def setup_work_dir():
    # create a working directory under home directory to store user data
    # the directory is named according to the current time starting with Mjolnir_, following by current time, ending with a random SHA1 hash
    import os
    import datetime
    import uuid

    date_time = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base = os.environ.get("LOKI_WORKDIR_BASE", os.path.join(os.path.expanduser("~"), "loki_workdirs"))
    os.makedirs(base, exist_ok=True)
    work_dir = os.path.join(base, f"Loki_{date_time}_{str(uuid.uuid4())}")
    return work_dir


