"""Request bodies. Deliberately forgiving (defaults everywhere, unknown keys ignored): the real checks live in
the storage layer, which answers with the human-readable messages the UI shows."""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Body(BaseModel):
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True, populate_by_name=True)


class Login(Body):
    username: str = ""
    password: str = ""
    remember: bool = True


class Account(Body):
    current: str = ""
    username: str | None = None
    password: str | None = None


class PathBody(Body):
    path: str = ""


class NewFile(PathBody):
    content: str = ""


class Rename(PathBody):
    name: str = ""


class Paths(Body):
    paths: list[str] = []


class Move(Paths):
    dest: str = "/"
    copy_: bool = Field(False, alias="copy")        # `copy` would shadow a pydantic method
    on_conflict: str = "rename"


class Delete(Paths):
    permanent: bool = False


class Ids(Body):
    ids: list[str] | None = None


class TextPut(PathBody):
    content: str = ""
    sha: str | None = None
    force: bool = False


class UploadFileSpec(Body):
    id: str = ""
    size: int = 0


class UploadBegin(Body):
    files: list[UploadFileSpec] = []


class CommitFile(Body):
    id: str = ""
    path: str = ""
    mtime: float | None = None


class UploadCommit(Body):
    files: list[CommitFile] = []
    dirs: list[str] = []
    on_conflict: str = "rename"


class Compress(Paths):
    dest: str = "/"
    name: str | None = None
    on_conflict: str = "rename"


class Extract(PathBody):
    dest: str | None = None
    on_conflict: str = "rename"


class Share(PathBody):
    hours: int | None = None


class SettingsPut(Body):
    changes: dict[str, Any] = {}


class Admin(Body):
    encrypted: bool = False
