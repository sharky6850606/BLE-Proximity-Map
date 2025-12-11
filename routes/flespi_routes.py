from flask import Blueprint, request

from services.beacon_logic import simplify_message, latest_messages

flespi_bp = Blueprint("flespi", __name__)


@flespi_bp.route("/flespi", methods=["POST"])
def flespi_receiver():
    data = request.get_json(silent=True)
    if data is None:
        return "no json", 400

    if isinstance(data, dict):
        msgs = data.get("messages") or data.get("result") or [data]
    else:
        msgs = data

    count = 0
    for raw in msgs:
        if isinstance(raw, dict):
            simplified = simplify_message(raw)
            latest_messages[simplified["ident"]] = simplified
            count += 1

    print(f"Received {len(msgs)} msgs, processed {count}, tracking {len(latest_messages)} devices.")
    return "OK", 200
