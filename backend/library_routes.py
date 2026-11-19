"""Global library endpoints keep historical research and export routes intact."""

from uuid import UUID

from fastapi import FastAPI

from backend.schemas.library import (
    PackCreateInput,
    PackOrderInput,
    PackReferenceInput,
    PackRenameInput,
    PackVersionInput,
    ReferenceLocator,
    ReferencePack,
    SavedReference,
)
from backend.schemas.models import ExportRecord
from backend.services.library_export import export_library_pack


def register_library_routes(app: FastAPI, library):
    @app.get("/library")
    def library_state(run_id: UUID | None = None):
        return library.state(str(run_id) if run_id else None)

    @app.get("/library/presentation")
    def library_presentation():
        return {"previews": library.previews()}

    @app.put("/favorites", response_model=SavedReference)
    def favorite(value: ReferenceLocator):
        return library.favorite(value)

    @app.delete("/favorites/{reference_id}")
    def unfavorite(reference_id: UUID):
        library.unfavorite(str(reference_id))
        return {"id": str(reference_id), "favorite": False}

    @app.post("/packs", response_model=ReferencePack, status_code=201)
    def create_pack(value: PackCreateInput):
        return library.create_pack(value)

    @app.get("/packs/{pack_id}", response_model=ReferencePack)
    def get_pack(pack_id: UUID):
        with library.repository.connection() as conn:
            return library.pack(conn, str(pack_id))

    @app.put("/packs/{pack_id}/activate", response_model=ReferencePack)
    def activate_pack(pack_id: UUID):
        return library.activate_pack(str(pack_id))

    @app.patch("/packs/{pack_id}", response_model=ReferencePack)
    def rename_pack(pack_id: UUID, value: PackRenameInput):
        return library.rename_pack(str(pack_id), value)

    @app.delete("/packs/{pack_id}")
    def delete_pack(pack_id: UUID, value: PackVersionInput):
        library.delete_pack(str(pack_id), value.expected_version)
        return {"id": str(pack_id), "deleted": True}

    @app.post("/packs/{pack_id}/references", response_model=ReferencePack)
    def add_reference(pack_id: UUID, value: PackReferenceInput):
        return library.add_reference(str(pack_id), value)

    @app.delete("/packs/{pack_id}/references/{reference_id}", response_model=ReferencePack)
    def remove_reference(pack_id: UUID, reference_id: UUID, value: PackVersionInput):
        return library.remove_reference(str(pack_id), str(reference_id), value.expected_version)

    @app.put("/packs/{pack_id}/order", response_model=ReferencePack)
    def reorder_pack(pack_id: UUID, value: PackOrderInput):
        return library.reorder_pack(str(pack_id), value)

    @app.post("/packs/{pack_id}/exports/pptx", response_model=ExportRecord, status_code=201)
    def export_pack(pack_id: UUID, value: PackVersionInput):
        return export_library_pack(library, str(pack_id), value.expected_version)
