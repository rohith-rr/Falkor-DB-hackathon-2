import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..config import settings

logger = logging.getLogger("wayfinder.github.client")

GITHUB_GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
GITHUB_REST_ENDPOINT = "https://api.github.com"
RAW_GITHUB_ENDPOINT = "https://raw.githubusercontent.com"

class GitHubClient:
    """Client for GitHub GraphQL & REST APIs using personal access token (PAT)."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.github_token
        self._session = httpx.Client(timeout=30.0)

    @property
    def headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Wayfinder-Company-Brain/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def close(self):
        self._session.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    def fetch_raw_file(self, repo: str, path: str, ref: str = "master") -> str:
        """Fetch raw file content from GitHub repository."""
        # First try raw.githubusercontent.com
        raw_url = f"{RAW_GITHUB_ENDPOINT}/{repo}/{ref}/{path}"
        resp = self._session.get(raw_url, headers=self.headers)
        if resp.status_code == 200:
            return resp.text

        # Fallback to GitHub REST contents API if raw returns 404 or auth required
        api_url = f"{GITHUB_REST_ENDPOINT}/repos/{repo}/contents/{path}?ref={ref}"
        resp = self._session.get(api_url, headers={**self.headers, "Accept": "application/vnd.github.raw+json"})
        resp.raise_for_status()
        return resp.text

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    def execute_graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute a GraphQL query against the GitHub API."""
        if not self.token:
            logger.warning("Executing GraphQL without GITHUB_TOKEN; rate limits will be restricted.")
        
        payload = {"query": query, "variables": variables or {}}
        resp = self._session.post(GITHUB_GRAPHQL_ENDPOINT, json=payload, headers=self.headers)
        
        # Check rate limit headers
        remaining = resp.headers.get("x-ratelimit-remaining")
        if remaining and int(remaining) < 5:
            reset_time = int(resp.headers.get("x-ratelimit-reset", 0))
            sleep_duration = max(0, reset_time - int(time.time())) + 1
            logger.warning(f"GitHub rate limit almost exhausted. Sleeping for {sleep_duration}s...")
            time.sleep(sleep_duration)

        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            logger.error(f"GraphQL Errors: {data['errors']}")
            raise RuntimeError(f"GraphQL error: {data['errors']}")
        return data.get("data", {})

    def search_sig_node_activity(
        self,
        repo: str = "kubernetes/kubernetes",
        label: str = "sig/node",
        since_date: Optional[str] = None,
        first: int = 30,
        after_cursor: Optional[str] = None,
        kind: str = 'pr',
        until_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Query one activity type with sig/node label, paged by updatedAt.
        """
        since_date = since_date or (datetime.now(timezone.utc) - timedelta(days=365)).date().isoformat()
        if kind not in ('pr', 'issue'):
            raise ValueError('Activity kind must be pr or issue')
        if 'T' in since_date:
            since_date = datetime.fromisoformat(since_date.replace('Z', '+00:00')).isoformat(timespec='seconds')
        # Some GitHub token types return an empty ISSUE search when issues and
        # PRs are mixed. Always specify the type and paginate each independently.
        query_str = f'repo:{repo} label:"{label}" is:{kind} sort:updated-asc'
        if since_date:
            query_str += f" updated:>={since_date}"
        if until_date:
            query_str += f" updated:<={until_date}"

        gql = """
        query($queryStr: String!, $first: Int!, $after: String) {
          search(query: $queryStr, type: ISSUE, first: $first, after: $after) {
            issueCount
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              __typename
              ... on Issue {
                id
                number
                title
                state
                createdAt
                closedAt
                updatedAt
                url
                author { login __typename }
                labels(first: 15) { nodes { name } }
                body
                comments(first: 30) {
                  pageInfo { hasNextPage endCursor }
                  nodes {
                    id
                    createdAt
                    body
                    url
                    author { login __typename }
                  }
                }
              }
              ... on PullRequest {
                id
                number
                title
                state
                createdAt
                mergedAt
                updatedAt
                url
                additions
                deletions
                milestone { title }
                author { login __typename }
                labels(first: 15) { nodes { name } }
                body
                files(first: 50) {
                  pageInfo { hasNextPage endCursor }
                  nodes {
                    path
                    additions
                    deletions
                  }
                }
                reviews(first: 30) {
                  pageInfo { hasNextPage endCursor }
                  nodes {
                    id
                    state
                    submittedAt
                    url
                    author { login __typename }
                    body
                  }
                }
                reviewThreads(first: 30) {
                  pageInfo { hasNextPage endCursor }
                  nodes {
                    id path
                    comments(first: 30) {
                      pageInfo { hasNextPage endCursor }
                      nodes { id body createdAt url path author { login __typename } pullRequestReview { id } }
                    }
                  }
                }
                comments(first: 30) {
                  pageInfo { hasNextPage endCursor }
                  nodes {
                    id
                    createdAt
                    body
                    url
                    author { login __typename }
                  }
                }
              }
            }
          }
        }
        """
        result = self.execute_graphql(gql, {"queryStr": query_str, "first": min(first, 100), "after": after_cursor})
        for node in result.get('search', {}).get('nodes', []):
            self._paginate_connections(node)
        return result

    def _paginate_connections(self, node):
        fields = {'comments': 'id createdAt body url author { login __typename }',
                  'reviews': 'id state submittedAt body url author { login __typename }',
                  'files': 'path additions deletions',
                  'reviewThreads': 'id path comments(first: 30) { pageInfo { hasNextPage endCursor } nodes { id body createdAt url path author { login __typename } pullRequestReview { id } } }'}
        for connection, selection in fields.items():
            value = node.get(connection)
            if not value:
                continue
            page = value.get('pageInfo', {})
            while page.get('hasNextPage'):
                cursor = page.get('endCursor')
                if not cursor:
                    raise RuntimeError('Missing nested pagination cursor')
                gql = f'query($id: ID!, $after: String!) {{ node(id: $id) {{ ... on {node["__typename"]} {{ {connection}(first: 100, after: $after) {{ pageInfo {{ hasNextPage endCursor }} nodes {{ {selection} }} }} }} }} }}'
                fetched = self.execute_graphql(gql, {'id': node['id'], 'after': cursor})['node'][connection]
                value['nodes'].extend(fetched.get('nodes', []))
                page = fetched.get('pageInfo', {})
                if page.get('hasNextPage') and page.get('endCursor') == cursor:
                    raise RuntimeError('Nested pagination did not advance')
            value['pageInfo'] = page
        for thread in node.get('reviewThreads', {}).get('nodes', []):
            comments = thread['comments']
            page = comments.get('pageInfo', {})
            while page.get('hasNextPage'):
                cursor = page.get('endCursor')
                if not cursor:
                    raise RuntimeError('Missing review-comment cursor')
                result = self.execute_graphql('query($id:ID!,$after:String!) { node(id:$id) { ... on PullRequestReviewThread { '
                    'comments(first:100,after:$after) { pageInfo { hasNextPage endCursor } nodes { id body createdAt url path '
                    'author { login __typename } pullRequestReview { id } } } } } }', {'id':thread['id'],'after':cursor})
                fetched = result['node']['comments']
                comments['nodes'].extend(fetched.get('nodes', []))
                page = fetched.get('pageInfo', {})
                if page.get('hasNextPage') and page.get('endCursor')==cursor:
                    raise RuntimeError('Review-comment pagination did not advance')
            comments['pageInfo'] = page

    def fetch_review_threads(self, repo, number):
        owner, name = repo.split('/',1)
        result = self.execute_graphql('query($owner:String!,$name:String!,$number:Int!) { repository(owner:$owner,name:$name) { '
            'pullRequest(number:$number) { id reviewThreads(first:30) { pageInfo { hasNextPage endCursor } '
            'nodes { id path comments(first:30) { pageInfo { hasNextPage endCursor } nodes { id body createdAt url path '
            'author { login __typename } pullRequestReview { id } } } } } } } }', {'owner':owner,'name':name,'number':number})
        pr = result['repository']['pullRequest']
        if not pr:
            raise RuntimeError('Pull request unavailable; review coverage remains incomplete')
        pr['__typename']='PullRequest'
        self._paginate_connections(pr)
        return pr['reviewThreads']

    def iter_activity(self, since_date=None, page_size=100, limit=None):
        if limit is not None and limit <= 0:
            return
        start = datetime.fromisoformat((since_date or (datetime.now(timezone.utc)-timedelta(days=365)).isoformat()).replace('Z','+00:00'))
        if start.tzinfo is None: start=start.replace(tzinfo=timezone.utc)
        if since_date: start-=timedelta(minutes=5)
        end=datetime.now(timezone.utc)
        count=0; seen=set()
        def window(kind,lower,upper):
            cursor=None
            while True:
                first=min(page_size,100,limit-count) if limit is not None else min(page_size,100)
                result=self.search_sig_node_activity(since_date=lower.isoformat(timespec='seconds'),
                    until_date=upper.isoformat(timespec='seconds'),first=first,after_cursor=cursor,kind=kind)['search']
                if cursor is None and limit is None and result.get('issueCount',0)>=1000:
                    if (upper-lower).total_seconds()<=2:
                        raise RuntimeError('GitHub search interval cannot be exhausted; watermark must not advance')
                    middle=lower+(upper-lower)/2
                    yield from window(kind,lower,middle)
                    yield from window(kind,middle,upper)
                    return
                yield from result.get('nodes',[])
                page=result.get('pageInfo',{})
                if not page.get('hasNextPage'): return
                following=page.get('endCursor')
                if not following or following==cursor: raise RuntimeError('Activity pagination did not advance')
                cursor=following
        for kind in ('pr','issue'):
            for node in window(kind,start,end):
                key=node.get('id')
                if key in seen: continue
                seen.add(key)
                yield node
                count+=1
                if limit is not None and count>=limit: return

    def repository_tree(self, repo):
        response = self._session.get(f'{GITHUB_REST_ENDPOINT}/repos/{repo}', headers=self.headers)
        response.raise_for_status()
        branch = response.json()['default_branch']
        response = self._session.get(f'{GITHUB_REST_ENDPOINT}/repos/{repo}/git/trees/{branch}?recursive=1', headers=self.headers)
        response.raise_for_status()
        tree = response.json()
        if tree.get('truncated'):
            raise RuntimeError(f'Truncated source tree for {repo}')
        return tree['sha'], [entry['path'] for entry in tree['tree'] if entry['type'] == 'blob']

    def release_refs(self, repo):
        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        refs, page = [], 1
        while True:
            response = self._session.get(f'{GITHUB_REST_ENDPOINT}/repos/{repo}/releases',
                                         params={'per_page': 100, 'page': page}, headers=self.headers)
            response.raise_for_status()
            releases = response.json()
            if not releases:
                break
            for release in releases:
                date = release.get('published_at')
                if date and datetime.fromisoformat(date.replace('Z', '+00:00')) >= cutoff and not release['prerelease'] and not release['draft']:
                    refs.append(release['tag_name'])
            if any(r.get('published_at') and datetime.fromisoformat(r['published_at'].replace('Z', '+00:00')) < cutoff for r in releases):
                break
            page += 1
        # One immutable release tag per minor is sufficient for gate-stage history.
        import re
        latest = {}
        for ref in refs:
            match = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)', ref)
            if match:
                version = tuple(map(int, match.groups()))
                if version[:2] not in latest or version > latest[version[:2]][0]:
                    latest[version[:2]] = (version, ref)
        return [item[1] for _, item in sorted(latest.items())]
