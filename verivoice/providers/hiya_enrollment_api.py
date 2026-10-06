"""Hiya audio screening and durable identity/voiceprint creation."""
import base64
import time
from .enrollment_common import EnrollmentError
from .hiya_client import HiyaClient, segment
from .hiya_synthesis_api import HiyaSynthesisAPI

class HiyaEnrollmentAPI:
    def __init__(self, settings):
        self.settings = settings

    def upload_checked(self, wav):
        with HiyaClient(self.settings) as hiya:
            uploaded = hiya.request("POST", hiya.space_path + "/audios",
                                    {"file": base64.b64encode(wav).decode("ascii")})
            handle = uploaded["handle"]
            for _ in range(30):
                if uploaded.get("state") == "available":
                    break
                if uploaded.get("state") in {"failed", "error", "notAvailable"}:
                    raise EnrollmentError("Hiya could not process this recording. Please record again.")
                time.sleep(1)
                uploaded = hiya.request("GET", hiya.space_path + "/audios/" + segment(handle))
            else:
                raise EnrollmentError("Hiya audio processing timed out. Please retry shortly.")
            detector = HiyaSynthesisAPI(hiya)
            result = detector.verify(handle)
            for _ in range(30):
                if result.state == "performed":
                    break
                if not result.verification_handle or result.state in {"failed", "error"}:
                    break
                time.sleep(1)
                result = detector.get_result(result.verification_handle)
            # Prototype threshold, not a calibrated liveness guarantee.
            if result.non_synthetic_score is None:
                raise EnrollmentError("Hiya did not return a completed non-synthetic score. This is an unavailable result, not a judgment about your voice. Please retry shortly.")
            if result.non_synthetic_score < 0.5:
                raise EnrollmentError(
                    f"Hiya non-synthetic score: {result.non_synthetic_score:.2f}; "
                    "VeriVoice's current enrollment cutoff: 0.50. "
                    "This recording did not meet the prototype cutoff; this does not establish that your voice is synthetic. Please record again.")
            return handle

    def finish(self, identity, audios):
        with HiyaClient(self.settings) as hiya:
            def ensure(path, collection, body):
                response = hiya.http.get(path)
                if response.status_code == 404:
                    return hiya.request("POST", collection, body)
                if response.is_error:
                    raise EnrollmentError(f"Hiya resource check failed (HTTP {response.status_code}).")
                return response.json()
            base = hiya.space_path
            ensure(base + "/identities/" + segment(identity), base + "/identities",
                   {"handle": identity})
            path = base + "/voiceprints/" + segment(identity) + "/main"
            vp = ensure(path, base + "/voiceprints", {"handle": "main", "identity": identity,
                        "model": "digital/v1", "minAudios": 5})
            if vp.get("state") != "computed":
                # Read existing members so resuming a partial operation never double-adds.
                response = hiya.http.get(path + "/audios", params={"page-size": 50})
                if response.is_error:
                    raise EnrollmentError("Could not inspect Hiya voiceprint recordings. Please retry.")
                members = response.json()
                if not isinstance(members, list) or int(response.headers.get("Page-Total", "1")) > 1:
                    raise EnrollmentError("Unexpected Hiya audio-list response; enrollment is not complete.")
                attached = {x.get("handle") for x in members}
                if not attached.issubset(set(audios)):
                    raise EnrollmentError("Hiya voiceprint contains unexpected recordings; enrollment stopped.")
                for audio in audios:
                    if audio not in attached:
                        hiya.request("POST", path + "/audios/" + segment(audio))
                vp = hiya.request("GET", path)
                if vp.get("state") == "computable":
                    hiya.request("POST", path + ":compute")
                elif vp.get("state") != "computed":
                    raise EnrollmentError("Hiya cannot compute this voiceprint yet. Check the recordings in the Hiya console.")
            for _ in range(30):
                vp = hiya.request("GET", path)
                if vp.get("state") == "computed":
                    if vp.get("audios") != 5:
                        raise EnrollmentError("Unexpected Hiya recording count; enrollment is not complete.")
                    return
                time.sleep(1)
            raise EnrollmentError("Hiya has not confirmed a computed voiceprint. Retry Finish shortly.")
