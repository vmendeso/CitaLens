from fastapi import Response
from pydantic import BaseModel

from packages.common.web import create_app
from packages.document_processing.report import render_report

app = create_app("report-service", internal=True)


class ReportRequest(BaseModel):
    snapshot: dict


@app.post("/render", responses={200: {"content": {"application/pdf": {}}}})
def render(request: ReportRequest):
    return Response(render_report(request.snapshot), media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="relatorio.pdf"'})
