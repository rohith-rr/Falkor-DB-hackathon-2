import logging
import re
from urllib.parse import urlparse
from ..parsers.owners_parser import OwnersParser
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Set
from ..models.common import (
    Authority,
    LinkMethod,
    SourceSystem,
    compute_content_hash,
    now_iso,
)
from ..models.layer1 import (
    Comment,
    Directory,
    File,
    Issue,
    Person,
    PullRequest,
    Review,
    Thread,
)
from ..parsers.prow_parser import ProwParser

logger = logging.getLogger("wayfinder.github.activity")

KNOWN_BOTS = {
    "kubernetes-prow",
    "k8s-ci-robot",
    "k8s-triage-robot",
    "k8s-reviewable",
    "fejta-bot",
    "k8s-bot",
    "dependabot",
    "github-actions",
}


def refresh_bot_flags(db):
    """Correct legacy person metadata using known bot logins; never unmark bots."""
    changed = 0
    result = db.query('MATCH (p:Person) RETURN p.id, p.login, p.is_bot')
    for ident, login, is_bot in result.result_set:
        if not is_bot and ActivityProcessor.is_bot_user(login):
            db.query('MATCH (p:Person {id: $id}) SET p.is_bot = true', {'id': ident})
            changed += 1
    return changed

class ActivityProcessor:
    """Transforms raw GitHub issues, PRs, reviews, and comments into Wayfinder graph elements."""

    @staticmethod
    def is_bot_user(login: Optional[str]) -> bool:
        if not login:
            return False
        login_lower = login.lower()
        if "[bot]" in login_lower or login_lower.endswith("-bot") or login_lower.endswith("-robot"):
            return True
        return login_lower in KNOWN_BOTS

    @classmethod
    def process_github_node(
        cls,
        raw_node: Dict[str, Any],
        repo: str = "kubernetes/kubernetes",
    ) -> Dict[str, Any]:
        """
        Process a single GitHub Issue or PullRequest node from GraphQL search.
        Returns extracted nodes and edges.
        """
        now = now_iso()
        typename = raw_node.get("__typename")
        number = raw_node.get("number")
        title = raw_node.get("title", "")
        state = raw_node.get("state", "OPEN")
        created_at = raw_node.get("createdAt")
        updated_at = raw_node.get("updatedAt")
        url = raw_node.get("url", f"https://github.com/{repo}/issues/{number}")
        body = raw_node.get("body", "") or ""
        url_parts = urlparse(url).path.strip('/').split('/')
        if len(url_parts) >= 2:
            repo = '/'.join(url_parts[:2])
        labels = [l.get("name") for l in raw_node.get("labels", {}).get("nodes", []) if l.get("name")]

        nodes: Dict[str, List[Any]] = {
            "people": [],
            "issues": [],
            "prs": [],
            "reviews": [],
            "comments": [],
            "threads": [],
            "files": [],
            "directories": [],
        }
        edges: List[Dict[str, Any]] = []

        people_seen: Dict[str, Person] = {}
        actors = [raw_node.get('author')] + [n.get('author') for group in ('comments', 'reviews')
                  for n in raw_node.get(group, {}).get('nodes', [])]
        api_bots = {a['login'].lower() for a in actors if a and a.get('__typename') == 'Bot'}

        def get_or_create_person(login: Optional[str], act_time: str) -> Optional[str]:
            if not login:
                return None
            pid = f"k8s:person:{login.lower()}"
            is_bot = cls.is_bot_user(login) or login.lower() in api_bots
            if pid not in people_seen:
                people_seen[pid] = Person(
                    id=pid,
                    login=login,
                    is_bot=is_bot,
                    first_seen_at=act_time,
                    last_active_at=act_time,
                    source_system=SourceSystem.GITHUB.value,
                    source_url=f"https://github.com/{login}",
                    source_ref=f"github:{login}",
                    observed_at=now,
                    valid_from=act_time or now,
                )
            else:
                p = people_seen[pid]
                if act_time and (not p.last_active_at or act_time > p.last_active_at):
                    p.last_active_at = act_time
            return pid

        author_login = raw_node.get("author", {}).get("login") if raw_node.get("author") else None
        author_id = get_or_create_person(author_login, created_at)

        target_id = ""
        target_label = ""

        # Process PR or Issue
        if typename == "PullRequest":
            target_label = "PullRequest"
            target_id = f"k8s:pr:{repo}:{number}"
            merged_at = raw_node.get("mergedAt")
            milestone = raw_node.get("milestone", {}).get("title") if raw_node.get("milestone") else None
            additions = raw_node.get("additions", 0)
            deletions = raw_node.get("deletions", 0)

            pr_node = PullRequest(
                id=target_id,
                number=number,
                repo=repo,
                title=title,
                state=state,
                merged_at=merged_at,
                labels=labels,
                milestone=milestone,
                body_ref=f"{target_id}#body",
                body_text=body,
                additions=additions,
                deletions=deletions,
                source_system=SourceSystem.GITHUB.value,
                source_url=url,
                source_ref=f"{repo}#{number}",
                source_updated_at=updated_at,
                observed_at=now,
                valid_from=created_at or now,
            )
            nodes["prs"].append(pr_node)

            # Files touched: (PullRequest)-[:TOUCHES]->(File) and (Directory)-[:CONTAINS]->(File)
            for file_entry in raw_node.get("files", {}).get("nodes", []) or []:
                fpath = file_entry.get("path")
                if not fpath:
                    continue
                file_id = f"k8s:file:{repo}:{fpath}"
                file_node = File(
                    id=file_id,
                    path=fpath,
                    repo=repo,
                    source_system=SourceSystem.GITHUB.value,
                    source_url=f"https://github.com/{repo}/blob/master/{fpath}",
                    source_ref=f"{repo}:{fpath}",
                    observed_at=now,
                    valid_from=now,
                )
                nodes["files"].append(file_node)

                # Directory containing file: (Directory)-[:CONTAINS]->(File)
                p_obj = PurePosixPath(fpath.replace("\\", "/"))
                dir_path = str(p_obj.parent) if str(p_obj.parent) != "." else ""
                dir_id = f"k8s:dir:{repo}:{dir_path}"
                dir_node = Directory(
                    id=dir_id,
                    path=dir_path,
                    repo=repo,
                    source_system=SourceSystem.GITHUB.value,
                    source_url=f"https://github.com/{repo}/tree/master/{dir_path}",
                    source_ref=f"{repo}:{dir_path}",
                    observed_at=now,
                    valid_from=now,
                )
                nodes["directories"].append(dir_node)
                hierarchy = OwnersParser.parse_owners_file('{}', repo, (dir_path + '/OWNERS').lstrip('/'))
                nodes['directories'].extend(hierarchy['directories'])
                edges.extend(e for e in hierarchy['edges'] if e['rel_type'] == 'CHILD_OF')

                edges.append({
                    "source_label": "Directory",
                    "source_id": dir_id,
                    "target_label": "File",
                    "target_id": file_id,
                    "rel_type": "CONTAINS",
                    "props": {
                        "id": f"{dir_id}->CONTAINS->{file_id}",
                        "source_url": url,
                        "source_ref": f"{repo}:{fpath}",
                        "observed_at": now,
                        "valid_from": created_at or now,
                        "link_method": LinkMethod.STRUCTURED_FILE.value,
                        "authority": Authority.CONFIRMED.value,
                    },
                })

                edges.append({
                    "source_label": "PullRequest",
                    "source_id": target_id,
                    "target_label": "File",
                    "target_id": file_id,
                    "rel_type": "TOUCHES",
                    "props": {
                        "id": f"{target_id}->TOUCHES->{file_id}",
                        "additions": file_entry.get("additions", 0),
                        "deletions": file_entry.get("deletions", 0),
                        "source_url": url,
                        "source_ref": f"{repo}#{number}",
                        "observed_at": now,
                        "valid_from": created_at or now,
                        "link_method": LinkMethod.API.value,
                        "authority": Authority.CONFIRMED.value,
                    },
                })

            # Reviews: (Person)-[:SUBMITTED]->(Review)-[:REVIEWS]->(PullRequest)
            for r_entry in raw_node.get("reviews", {}).get("nodes", []) or []:
                r_id_raw = r_entry.get("id")
                r_state = r_entry.get("state", "COMMENTED")
                r_time = r_entry.get("submittedAt", now)
                r_body = r_entry.get("body", "") or ""
                r_author = r_entry.get("author", {}).get("login") if r_entry.get("author") else None
                r_author_id = get_or_create_person(r_author, r_time)

                review_id = f"k8s:review:{repo}:{r_id_raw}"
                rev_node = Review(
                    id=review_id,
                    state=r_state,
                    submitted_at=r_time,
                    body_ref=f"{review_id}#body",
                    body_text=r_body,
                    source_system=SourceSystem.GITHUB.value,
                    source_url=r_entry.get("url", url),
                    source_ref=f"{repo}#review:{r_id_raw}",
                    observed_at=now,
                    valid_from=r_time,
                )
                nodes["reviews"].append(rev_node)

                # Edge (Review)-[:REVIEWS]->(PullRequest)
                edges.append({
                    "source_label": "Review",
                    "source_id": review_id,
                    "target_label": "PullRequest",
                    "target_id": target_id,
                    "rel_type": "REVIEWS",
                    "props": {
                        "id": f"{review_id}->REVIEWS->{target_id}",
                        "state": r_state,
                        "at": r_time,
                        "source_url": url,
                        "source_ref": f"{repo}#review:{r_id_raw}",
                        "observed_at": now,
                        "valid_from": r_time,
                        "link_method": LinkMethod.API.value,
                        "authority": Authority.CONFIRMED.value,
                    },
                })

                # Edge (Person)-[:SUBMITTED]->(Review)
                if r_author_id:
                    edges.append({
                        "source_label": "Person",
                        "source_id": r_author_id,
                        "target_label": "Review",
                        "target_id": review_id,
                        "rel_type": "SUBMITTED",
                        "props": {
                            "id": f"{r_author_id}->SUBMITTED->{review_id}",
                            "state": r_state,
                            "at": r_time,
                            "source_url": url,
                            "source_ref": f"{repo}#review:{r_id_raw}",
                            "observed_at": now,
                            "valid_from": r_time,
                            "link_method": LinkMethod.API.value,
                            "authority": Authority.CONFIRMED.value,
                        },
                    })

        else:
            # Issue
            target_label = "Issue"
            target_id = f"k8s:issue:{repo}:{number}"
            closed_at = raw_node.get("closedAt")

            issue_node = Issue(
                id=target_id,
                number=number,
                repo=repo,
                title=title,
                state=state,
                labels=labels,
                created_at=created_at or now,
                closed_at=closed_at,
                body_ref=f"{target_id}#body",
                body_text=body,
                source_system=SourceSystem.GITHUB.value,
                source_url=url,
                source_ref=f"{repo}#{number}",
                source_updated_at=updated_at,
                observed_at=now,
                valid_from=created_at or now,
            )
            nodes["issues"].append(issue_node)

        # Author edge: (Person)-[:AUTHORED]->(PullRequest | Issue)
        if author_id:
            edges.append({
                "source_label": "Person",
                "source_id": author_id,
                "target_label": target_label,
                "target_id": target_id,
                "rel_type": "AUTHORED",
                "props": {
                    "id": f"{author_id}->AUTHORED->{target_id}",
                    "at": created_at or now,
                    "source_url": url,
                    "source_ref": f"{repo}#{number}",
                    "observed_at": now,
                    "valid_from": created_at or now,
                    "link_method": LinkMethod.API.value,
                    "authority": Authority.CONFIRMED.value,
                },
            })

        def add_references(text, source_label, source_id, source_url, timestamp):
            for match in re.finditer(r'https?://github\.com/[\w-]+/[\w-]+/(?:issues|pull)/\d+#(?:issuecomment|discussion_r)-\d+', text):
                comment_url = match.group(0)
                edges.append({'source_label': source_label, 'source_id': source_id, 'target_label': 'Comment',
                    'target_id': comment_url, 'target_url': comment_url, 'rel_type': 'REFERENCES',
                    'props': {'id': f'{source_id}->REFERENCES->{comment_url}', 'pattern': comment_url,
                    'source_url': source_url, 'source_ref': source_id, 'valid_from': timestamp or now,
                    'observed_at': now, 'link_method': LinkMethod.EXACT_REFERENCE.value, 'authority': Authority.CONFIRMED.value}})
            refs = ProwParser.extract_references(text)
            links = []
            for num in refs['issues']:
                links.append(('Issue', f'k8s:issue:{repo}:{num}', 'REFERENCES', f'#{num}'))
            for num in refs['keps']:
                links.append(('KEP', f'k8s:kep:{num}', 'REFERENCES', f'KEP-{num}'))
            for ref_url in refs['urls']:
                parts = urlparse(ref_url).path.strip('/').split('/')
                ref_repo = '/'.join(parts[:2])
                kind = 'PullRequest' if parts[2] == 'pull' else 'Issue'
                marker = 'pr' if kind == 'PullRequest' else 'issue'
                links.append((kind, f'k8s:{marker}:{ref_repo}:{parts[3]}', 'REFERENCES', ref_url))
            # Keep the repository and entity type from closing URLs.
            for match in re.finditer(r'(?i)\b(?:fixes|closes|resolves)\s+(https?://github\.com/[\w-]+/[\w-]+/(?:issues|pull)/\d+|#\d+)', text):
                token = match.group(1)
                if token.startswith('#'):
                    kind, tid = 'Issue', f'k8s:issue:{repo}:{token[1:]}'
                else:
                    parts = urlparse(token).path.strip('/').split('/')
                    kind = 'PullRequest' if parts[2] == 'pull' else 'Issue'
                    tid = f"k8s:{'pr' if kind == 'PullRequest' else 'issue'}:{'/'.join(parts[:2])}:{parts[3]}"
                links.append((kind, tid, 'FIXES', token))
            for kind, tid, rel, pattern in links:
                edges.append({'source_label': source_label, 'source_id': source_id, 'target_label': kind,
                    'target_id': tid, 'rel_type': rel, 'props': {'id': f'{source_id}->{rel}->{tid}',
                    'pattern': pattern, 'source_url': source_url, 'source_ref': source_id,
                    'observed_at': now, 'valid_from': timestamp or now,
                    'link_method': LinkMethod.EXACT_REFERENCE.value, 'authority': Authority.CONFIRMED.value}})
        add_references(body, target_label, target_id, url, created_at)

        # Thread for this Issue/PR
        thread_id = f"k8s:thread:{repo}:{number}"
        comments_list = raw_node.get("comments", {}).get("nodes", []) or []
        thread_node = Thread(
            id=thread_id,
            kind="pull_request" if typename == "PullRequest" else "issue",
            root_ref=target_id,
            comment_count=len(comments_list),
            last_activity_at=updated_at or created_at,
            source_system=SourceSystem.GITHUB.value,
            source_url=url,
            source_ref=f"{repo}#{number}",
            observed_at=now,
            valid_from=created_at or now,
        )
        nodes["threads"].append(thread_node)

        # Edge: (Thread)-[:ON]->(PullRequest | Issue)
        edges.append({
            "source_label": "Thread",
            "source_id": thread_id,
            "target_label": target_label,
            "target_id": target_id,
            "rel_type": "ON",
            "props": {
                "id": f"{thread_id}->ON->{target_id}",
                "source_url": url,
                "source_ref": f"{repo}#{number}",
                "observed_at": now,
                "valid_from": created_at or now,
                "link_method": LinkMethod.API.value,
                "authority": Authority.CONFIRMED.value,
            },
        })

        # Process Comments
        prev_comment_id: Optional[str] = None
        prev_author = prev_body = None
        for pos, c_entry in enumerate(sorted(comments_list, key=lambda c: c.get("createdAt") or "")):
            c_raw_id = c_entry.get("id")
            c_body = c_entry.get("body", "") or ""
            c_time = c_entry.get("createdAt", now)
            c_url = c_entry.get("url", url)
            c_author = c_entry.get("author", {}).get("login") if c_entry.get("author") else None
            c_author_id = get_or_create_person(c_author, c_time)

            prow_commands = ProwParser.parse_commands(c_body)
            is_cmd_only = ProwParser.is_command_only(c_body)

            comment_id = f"k8s:comment:{repo}:{c_raw_id}"
            comment_node = Comment(
                id=comment_id,
                kind="issue" if typename == "Issue" else "review",
                created_at=c_time,
                body_ref=f"{comment_id}#body",
                body_text=c_body,
                is_command=is_cmd_only,
                commands=prow_commands,
                activity_category=ProwParser.classify(c_body)['category'],
                attribution='pr_wide',
                pr_id=target_id if typename == 'PullRequest' else None,
                source_system=SourceSystem.GITHUB.value,
                source_url=c_url,
                source_ref=f"{repo}#comment:{c_raw_id}",
                observed_at=now,
                valid_from=c_time,
            )
            nodes["comments"].append(comment_node)

            # Edge: (Thread)-[:HAS_COMMENT {position}]->(Comment)
            edges.append({
                "source_label": "Thread",
                "source_id": thread_id,
                "target_label": "Comment",
                "target_id": comment_id,
                "rel_type": "HAS_COMMENT",
                "props": {
                    "id": f"{thread_id}->HAS_COMMENT->{comment_id}",
                    "position": pos,
                    "source_url": c_url,
                    "source_ref": f"{repo}#comment:{c_raw_id}",
                    "observed_at": now,
                    "valid_from": c_time,
                    "link_method": LinkMethod.API.value,
                    "authority": Authority.CONFIRMED.value,
                },
            })

            # Edge: (Person)-[:AUTHORED]->(Comment)
            if c_author_id:
                edges.append({
                    "source_label": "Person",
                    "source_id": c_author_id,
                    "target_label": "Comment",
                    "target_id": comment_id,
                    "rel_type": "AUTHORED",
                    "props": {
                        "id": f"{c_author_id}->AUTHORED->{comment_id}",
                        "at": c_time,
                        "source_url": c_url,
                        "source_ref": f"{repo}#comment:{c_raw_id}",
                        "observed_at": now,
                        "valid_from": c_time,
                        "link_method": LinkMethod.API.value,
                        "authority": Authority.CONFIRMED.value,
                    },
                })

                # Prow /approve and /lgtm handling
                if typename == "PullRequest":
                    for cmd in prow_commands:
                        if cmd == "approve":
                            edges.append({
                                "source_label": "Person",
                                "source_id": c_author_id,
                                "target_label": "PullRequest",
                                "target_id": target_id,
                                "rel_type": "APPROVED",
                                "props": {
                                    "id": f"{c_author_id}->APPROVED->{target_id}",
                                    "via_comment": comment_id,
                                    "at": c_time,
                                    "source_url": c_url,
                                    "source_ref": f"{repo}#comment:{c_raw_id}",
                                    "observed_at": now,
                                    "valid_from": c_time,
                                    "link_method": LinkMethod.EXACT_REFERENCE.value,
                                    "authority": Authority.CONFIRMED.value,
                                },
                            })
                        elif cmd == "lgtm":
                            edges.append({
                                "source_label": "Person",
                                "source_id": c_author_id,
                                "target_label": "PullRequest",
                                "target_id": target_id,
                                "rel_type": "LGTM",
                                "props": {
                                    "id": f"{c_author_id}->LGTM->{target_id}",
                                    "via_comment": comment_id,
                                    "at": c_time,
                                    "source_url": c_url,
                                    "source_ref": f"{repo}#comment:{c_raw_id}",
                                    "observed_at": now,
                                    "valid_from": c_time,
                                    "link_method": LinkMethod.EXACT_REFERENCE.value,
                                    "authority": Authority.CONFIRMED.value,
                                },
                            })

            # Edge: (Comment)-[:REPLIES_TO]->(Comment) in sequential thread
            explicit_reply = c_entry.get('replyTo')
            reply_id = f"k8s:comment:{repo}:{explicit_reply['id']}" if explicit_reply else prev_comment_id
            mention = prev_author and re.search(r'@' + re.escape(prev_author) + r'\b', c_body, re.I)
            quoted = prev_body and any(line.lstrip('> ').strip() and line.lstrip('> ').strip() in prev_body
                                      for line in c_body.splitlines() if line.lstrip().startswith('>'))
            if reply_id and (explicit_reply or mention or quoted):
                edges.append({
                    "source_label": "Comment",
                    "source_id": comment_id,
                    "target_label": "Comment",
                    "target_id": reply_id,
                    "rel_type": "REPLIES_TO",
                    "props": {
                        "id": f"{comment_id}->REPLIES_TO->{reply_id}",
                        "source_url": c_url,
                        "source_ref": f"{repo}#comment:{c_raw_id}",
                        "observed_at": now,
                        "valid_from": c_time,
                        "link_method": LinkMethod.API.value if explicit_reply else LinkMethod.EXACT_REFERENCE.value,
                        "authority": Authority.CONFIRMED.value,
                    },
                })
            add_references(c_body, 'Comment', comment_id, c_url, c_time)
            prev_comment_id = comment_id
            prev_author, prev_body = c_author, c_body

        for raw_thread in raw_node.get('reviewThreads', {}).get('nodes', []):
            tid=f"k8s:review-thread:{repo}:{raw_thread['id']}"
            entries=raw_thread.get('comments',{}).get('nodes',[])
            nodes['threads'].append(Thread(id=tid,kind='review_line',root_ref=target_id,comment_count=len(entries),
                source_system='github',source_url=url,source_ref=raw_thread['id'],observed_at=now))
            def link(sl,sid,tl,target,kind,source_url,ref):
                edges.append({'source_label':sl,'source_id':sid,'target_label':tl,'target_id':target,'rel_type':kind,
                    'props':{'id':f'{sid}->{kind}->{target}','source_url':source_url,'source_ref':ref,
                             'observed_at':now,'valid_from':now,'link_method':'API','authority':'CONFIRMED'}})
            link('Thread',tid,'PullRequest',target_id,'ON',url,raw_thread['id'])
            for entry in entries:
                cid=f"k8s:comment:{repo}:{entry['id']}"
                login=entry.get('author',{}).get('login') if entry.get('author') else None
                if (entry.get('author') or {}).get('__typename')=='Bot' and login:
                    api_bots.add(login.lower())
                pid=get_or_create_person(login,entry.get('createdAt'))
                text=entry.get('body') or ''
                path=entry.get('path') or raw_thread.get('path')
                review=entry.get('pullRequestReview')
                rid=f"k8s:review:{repo}:{review['id']}" if review else None
                nodes['comments'].append(Comment(id=cid,kind='review_line',created_at=entry['createdAt'],body_text=text,
                    body_ref=cid+'#body',is_command=ProwParser.is_command_only(text),commands=ProwParser.parse_commands(text),
                    activity_category=ProwParser.classify(text)['category'],attribution='file_specific',file_path=path,
                    pr_id=target_id,review_id=rid,source_system='github',source_url=entry['url'],source_ref=entry['id'],observed_at=now))
                link('Thread',tid,'Comment',cid,'HAS_COMMENT',entry['url'],entry['id'])
                link('Comment',cid,'PullRequest',target_id,'ON_PR',entry['url'],entry['id'])
                if pid:
                    link('Person',pid,'Comment',cid,'AUTHORED',entry['url'],entry['id'])
                if path:
                    link('Comment',cid,'File',f'k8s:file:{repo}:{path}','ON_FILE',entry['url'],entry['id'])
                if rid:
                    link('Comment',cid,'Review',rid,'IN_REVIEW',entry['url'],entry['id'])
        nodes["people"] = list(people_seen.values())
        return {"nodes": nodes, "edges": edges}
