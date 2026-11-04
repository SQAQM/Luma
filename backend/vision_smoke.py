"""Frozen-runtime check: generated geometry only, with an explicit model path."""
import hashlib
import json
import tempfile
import time
from pathlib import Path
from PIL import Image, ImageDraw
from .vision import LocalContentClassifier


def run(model_directory,report_path):
    from desktop.service import LibraryService
    report={"verification":"failed","fixtures":"four generated geometry images only; no default catalog or user images", "accuracy_dataset_validated":False}
    try:
        engine=LocalContentClassifier(Path(model_directory).absolute())
        if not engine.status()["ready"]:
            raise RuntimeError("The explicitly selected model is not ready")
        with tempfile.TemporaryDirectory(prefix="luma-vision-smoke-") as temporary:
            root=Path(temporary)
            source=root/"synthetic"
            source.mkdir()
            for name in ("circle","square","landscape","blank"):
                image=Image.new("RGB",(256,256),"white")
                draw=ImageDraw.Draw(image)
                if name=="circle": draw.ellipse((50,50,206,206),fill="blue")
                elif name=="square": draw.rectangle((50,50,206,206),fill="red")
                elif name=="landscape":
                    draw.rectangle((0,0,256,180),fill="skyblue")
                    draw.rectangle((0,170,256,256),fill="forestgreen")
                    draw.polygon(((0,190),(100,55),(200,190)),fill="gray")
                image.save(source/f"{name}.png")
            before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
            service=LibraryService(root/"data")
            def wait(job):
                deadline=time.monotonic()+45
                while time.monotonic()<deadline:
                    result=service.job(job["id"])
                    if result["status"] not in ("queued","running"):
                        if result["status"]!="completed": raise RuntimeError(result["message"])
                        return result
                    time.sleep(.02)
                raise RuntimeError("Synthetic content task timed out")
            try:
                wait(service.import_directory(str(source)))
                service._vision=engine
                definitions=service.put_content_tags([
                    dict(name="蓝色圆形",description="a blue circle on a white background",threshold=.28,enabled=True),
                    dict(name="红色方形",description="a red square on a white background",threshold=.28,enabled=True),
                    dict(name="山景",description="a landscape with mountains and green fields under a blue sky",threshold=.28,enabled=True),
                ])
                result=wait(service.classify_contents(scope="all"))
                records=[]
                for image in service.query()["items"]:
                    records.append({"fixture":image["name"],"content_tags":image["content_tags"]})
                by_name={r["fixture"]:[t["name"] for t in r["content_tags"]] for r in records}
                if "蓝色圆形" not in by_name["circle.png"] or "红色方形" not in by_name["square.png"] or by_name["blank.png"]:
                    raise RuntimeError("Synthetic fixture match or abstention check failed")
                if any(hashlib.sha256(p.read_bytes()).hexdigest()!=before[p.name] for p in source.iterdir()):
                    raise RuntimeError("Synthetic source image bytes changed")
                report.update(verification="passed",status=engine.status(),records=records,job_status=result["status"],job_processed=result["processed"],definition_id_kind="SQLite integer",files_unchanged=True)
            finally:
                service.close()
    except Exception as exc:
        report["error"]=str(exc)
    output=Path(report_path).absolute()
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    return 0 if report["verification"]=="passed" else 1
