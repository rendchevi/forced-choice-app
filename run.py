"""Start the study: Potato (login, phases, export) plus the /chain/* routes.

    python run.py [sessions.yaml]

Participant URL:  http://<host>:<port>/?sid=<user_id>--spec=<spec>--chain=<k>
Monitoring:       http://<host>:<port>/chain/admin?key=<contents of admin_api_key.txt>
"""

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main(study_path="sessions.yaml"):
    os.chdir(ROOT)  # Potato resolves task_dir and every path in config.yaml from here
    sys.path.insert(0, str(ROOT))
    from potato.flask_server import create_app
    from potato.server_utils.admin_key import validate_admin_api_key
    from potato.server_utils.config_module import config as potato_config

    from chain_core import load_study
    from chain_server import ChainStudy, make_blueprint

    study, specs = load_study(study_path)
    chain = ChainStudy(study, specs)  # loads and checks every spec's assets before the server starts
    # create_app rebinds potato.flask_server.app, so the blueprint goes on the app it returns
    app = create_app(study["potato_config"])
    app.register_blueprint(make_blueprint(chain, lambda key: validate_admin_api_key(key, potato_config)))
    logging.getLogger(__name__).info("chain study %s: specs %s, data in %s", chain.study_id, list(specs), chain.root)
    app.run(host=study["host"], port=study["port"], threaded=True, use_reloader=False)


if __name__ == "__main__":
    main(*sys.argv[1:])
