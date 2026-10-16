from typing import List, Optional
from pydantic import Field
from .common import BaseNode


class Organization(BaseNode):
    name: str
    synthetic: bool = True


class Team(BaseNode):
    name: str
    organization_id: str


class Project(BaseNode):
    name: str
    organization_id: str


class Component(BaseNode):
    name: str
    organization_id: str


class SourceRevision(BaseNode):
    source_unit_id: str
    text_content: str


class Attachment(BaseNode):
    name: str
    content_type: Optional[str] = None
    text_content: Optional[str] = None

class Person(BaseNode):
    login: str
    display_name: Optional[str] = None
    is_bot: bool = False
    first_seen_at: Optional[str] = None
    last_active_at: Optional[str] = None

class SIG(BaseNode):
    name: str
    dir: Optional[str] = None
    charter_url: Optional[str] = None
    mission: Optional[str] = None

class Subproject(BaseNode):
    name: str
    description: Optional[str] = None

class Repository(BaseNode):
    owner: str
    name: str
    default_branch: str = "main"

class Directory(BaseNode):
    path: str
    repo: str

class File(BaseNode):
    path: str
    repo: str
    last_changed_at: Optional[str] = None

class OwnersFile(BaseNode):
    path: str
    commit_sha: Optional[str] = None
    no_parent_owners: bool = False
    labels: List[str] = Field(default_factory=list)

class KEP(BaseNode):
    number: str
    title: str
    status: str
    stage: Optional[str] = None
    owning_sig: str = "sig-node"
    latest_milestone: Optional[str] = None
    creation_date: Optional[str] = None
    last_updated: Optional[str] = None

class KEPSection(BaseNode):
    kep_number: str
    heading: str
    text_ref: str
    content_hash: str
    order: int = 0
    text_content: Optional[str] = None

class FeatureGate(BaseNode):
    name: str
    current_stage: str
    default_enabled: bool = False
    locked_to_default: bool = False

class Release(BaseNode):
    version: str
    released_at: Optional[str] = None

class Issue(BaseNode):
    number: int
    repo: str
    title: str
    state: str
    labels: List[str] = Field(default_factory=list)
    created_at: str
    closed_at: Optional[str] = None
    body_ref: Optional[str] = None
    body_text: Optional[str] = None

class PullRequest(BaseNode):
    number: int
    repo: str
    title: str
    state: str
    merged_at: Optional[str] = None
    labels: List[str] = Field(default_factory=list)
    milestone: Optional[str] = None
    body_ref: Optional[str] = None
    body_text: Optional[str] = None
    additions: int = 0
    deletions: int = 0

class Review(BaseNode):
    id: str
    state: str  # APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED
    submitted_at: str
    body_ref: Optional[str] = None
    body_text: Optional[str] = None

class Comment(BaseNode):
    id: str
    kind: str  # issue, review, review_line, forum
    created_at: str
    body_ref: Optional[str] = None
    body_text: Optional[str] = None
    is_command: bool = False
    commands: List[str] = Field(default_factory=list)
    activity_category: str = 'discussion'
    attribution: str = 'pr_wide'
    file_path: Optional[str] = None
    pr_id: Optional[str] = None
    review_id: Optional[str] = None

class Thread(BaseNode):
    kind: str  # issue, pull_request, review_line, forum_topic
    root_ref: str
    comment_count: int = 0
    last_activity_at: Optional[str] = None

class DocPage(BaseNode):
    path: str
    title: str
    last_edited_at: Optional[str] = None
    front_matter: Optional[str] = None

class DocSection(BaseNode):
    page_path: str
    heading: str
    text_ref: str
    content_hash: str
    order: int = 0
    text_content: Optional[str] = None
