"""Bundled SIG Node seed data representing actual Kubernetes SIG Node structure and activity."""

SIGS_YAML_SAMPLE = """
sigs:
  - name: node
    dir: sig-node
    mission: "To define, design, implement, and maintain node-level components such as kubelet, container runtime, and device plugins."
    charter_link: "https://github.com/kubernetes/community/blob/master/sig-node/charter.md"
    leadership:
      chairs:
        - github: dchen1107
          name: Dawn Chen
        - github: mrunalp
          name: Mrunal Patel
      tech_leads:
        - github: haircommander
          name: Peter Hunt
        - github: SergeyKanzhelev
          name: Sergey Kanzhelev
    subprojects:
      - name: cri-api
        description: "Container Runtime Interface definitions and API for kubelet."
        owners:
          - "https://raw.githubusercontent.com/kubernetes/kubernetes/master/staging/src/k8s.io/cri-api/OWNERS"
      - name: cAdvisor
        description: "Container metrics and resource analysis integration."
        owners:
          - "https://raw.githubusercontent.com/google/cadvisor/master/OWNERS"
      - name: kubelet
        description: "Node agent that manages container lifecycle, resources, and probes."
        owners:
          - "https://raw.githubusercontent.com/kubernetes/kubernetes/master/pkg/kubelet/OWNERS"
"""

OWNERS_ALIASES_SAMPLE = """
aliases:
  sig-node-approvers:
    - dchen1107
    - mrunalp
    - SergeyKanzhelev
  sig-node-reviewers:
    - haircommander
    - bobbypage
    - pacoxu
    - vinaykul
"""

OWNERS_KUBELET_SAMPLE = """
approvers:
  - sig-node-approvers
  - dchen1107
  - mrunalp
reviewers:
  - sig-node-reviewers
  - vinaykul
  - haircommander
labels:
  - sig/node
options:
  no_parent_owners: true
"""

KEP_1287_YAML_SAMPLE = """
title: In-place Update of Pod Resources
kep-number: 1287
authors:
  - "@vinaykul"
owning-sig: sig-node
participating-sigs:
  - sig-api-machinery
  - sig-scheduling
status: implementable
stage: beta
latest-milestone: "v1.30"
milestone:
  alpha: "v1.27"
  beta: "v1.30"
  stable: "v1.33"
feature-gates:
  - InPlacePodVerticalScaling
creation-date: "2019-06-18"
last-updated: "2024-04-01"
"""

KEP_1287_README_SAMPLE = """# KEP-1287: In-place Update of Pod Resources

## Summary
In-place Update of Pod Resources allows updating Container resources (CPU and memory requests and limits) without restarting the Pod or its Containers.

## Motivation
Historically, Pod resource requirements were immutable. Resizing required killing the Pod and scheduling a new one. In workloads like stateful databases or long-running AI inference, restarting a Pod causes significant disruption and downtime.

## Goals
- Allow changing CPU and Memory requests/limits without restarting containers.
- Work seamlessly with the Kubelet CRI resize APIs.
- Provide clear status reporting on allocated vs actual resources.

## Non-Goals
- Resizing ephemeral storage in place.
- Moving Pods across nodes when resizing exceeds node capacity.

## Proposal
Kubelet's pod resize controller detects changes in Pod Spec resources and invokes the CRI UpdateContainerResources API. Kubelet coordinates with cgroups to adjust limits and updates the Pod status.

## Alternatives
- Pod Mutation Webhook: Rejected because admission mutation cannot modify running pods without controller involvement.
- Restart-based resize: Rejected because it causes container downtime.
"""

KUBELET_FEATURES_GO_SAMPLE = """
package features

import "k8s.io/component-base/featuregate"

const (
    InPlacePodVerticalScaling featuregate.Feature = "InPlacePodVerticalScaling"
    MemoryQoS featuregate.Feature = "MemoryQoS"
    ContainerCheckpoint featuregate.Feature = "ContainerCheckpoint"
)

var defaultKubernetesFeatureGates = map[featuregate.Feature]featuregate.FeatureSpec{
    InPlacePodVerticalScaling: {Default: true, PreRelease: featuregate.Beta},
    MemoryQoS: {Default: false, PreRelease: featuregate.Alpha},
    ContainerCheckpoint: {Default: true, PreRelease: featuregate.GA, LockToDefault: true},
}
"""

DOC_PAGE_RESIZE_SAMPLE = """---
title: "In-Place Pod Vertical Scaling"
content_type: concept
stage: alpha
---
# In-Place Pod Vertical Scaling

Kubernetes allows updating Pod CPU and memory resources without restarting containers.
This feature is controlled by the `InPlacePodVerticalScaling` feature gate and was proposed in KEP-1287.

## How it works
When Pod spec is patched, the Kubelet resizes cgroups using the CRI runtime.
"""

from datetime import datetime, timezone, timedelta

def get_sample_github_activity():
    now = datetime.now(timezone.utc)
    t_20d = (now - timedelta(days=20)).isoformat()
    t_15d = (now - timedelta(days=15)).isoformat()
    t_10d = (now - timedelta(days=10)).isoformat()
    t_5d = (now - timedelta(days=5)).isoformat()
    t_2d = (now - timedelta(days=2)).isoformat()

    return [
        {
            "__typename": "PullRequest",
            "number": 118234,
            "title": "kubelet: implement in-place resize coordination with CRI",
            "state": "MERGED",
            "createdAt": t_20d,
            "mergedAt": t_2d,
            "updatedAt": t_2d,
            "url": "https://github.com/kubernetes/kubernetes/pull/118234",
            "author": {"login": "vinaykul"},
            "labels": {"nodes": [{"name": "sig/node"}, {"name": "size/L"}]},
            "body": "This PR implements in-place resource resize in pkg/kubelet. Fixes #112000. References KEP-1287.",
            "additions": 450,
            "deletions": 80,
            "milestone": {"title": "v1.30"},
            "files": {
                "nodes": [
                    {"path": "pkg/kubelet/kubelet.go", "additions": 250, "deletions": 40},
                    {"path": "pkg/kubelet/pod_resize.go", "additions": 200, "deletions": 40},
                ]
            },
            "reviews": {
                "nodes": [
                    {
                        "id": "PRR_kwDOA123",
                        "state": "APPROVED",
                        "submittedAt": t_5d,
                        "url": "https://github.com/kubernetes/kubernetes/pull/118234#pullrequestreview-123",
                        "author": {"login": "haircommander"},
                        "body": "The CRI coordination looks solid and aligns with KEP-1287. /lgtm",
                    }
                ]
            },
            "comments": {
                "nodes": [
                    {
                        "id": "IC_kwDOA456",
                        "createdAt": t_15d,
                        "url": "https://github.com/kubernetes/kubernetes/pull/118234#issuecomment-456",
                        "author": {"login": "bobbypage"},
                        "body": "My main concern is memory cgroup limits: what happens if memory resize fails due to OOM? We need to handle rollback safely.",
                    },
                    {
                        "id": "IC_kwDOA789",
                        "createdAt": t_10d,
                        "url": "https://github.com/kubernetes/kubernetes/pull/118234#issuecomment-789",
                        "author": {"login": "vinaykul"},
                        "body": "We addressed the memory rollback in commit 3a4f; if the runtime returns an error during cgroup limit expansion, kubelet reverts to the previous allocated limit.",
                    },
                    {
                        "id": "IC_kwDOA999",
                        "createdAt": t_5d,
                        "url": "https://github.com/kubernetes/kubernetes/pull/118234#issuecomment-999",
                        "author": {"login": "dchen1107"},
                        "body": "Rollback handling looks good. /approve",
                    },
                ]
            },
        },
        {
            "__typename": "Issue",
            "number": 112000,
            "title": "kubelet crashes or misbehaves during pod resource updates",
            "state": "CLOSED",
            "createdAt": t_20d,
            "closedAt": t_2d,
            "updatedAt": t_2d,
            "url": "https://github.com/kubernetes/kubernetes/issues/112000",
            "author": {"login": "vinaykul"},
            "labels": {"nodes": [{"name": "sig/node"}, {"name": "kind/bug"}]},
            "body": "Tracking issue for handling in-place pod resize edge cases in kubelet without pod restarts.",
            "comments": {
                "nodes": [
                    {
                        "id": "IC_kwDOA111",
                        "createdAt": t_15d,
                        "url": "https://github.com/kubernetes/kubernetes/issues/112000#issuecomment-111",
                        "author": {"login": "pacoxu"},
                        "body": "Does the CRI runtime handle cpu.cfs_quota_us updates atomically without dropping thread scheduling?",
                    }
                ]
            },
        },
    ]

SAMPLE_GITHUB_ACTIVITY = get_sample_github_activity()
