"""``pskit remove --keep-resources``: destroy functions and triggers, keep the data.

Managed buckets, queues and topics are taken out of the Terraform state before
``terraform destroy``, so they stay in the cloud (with their objects and
messages) and pskit stops managing them. Providers name those resources
``b-<bucket>``, ``q-<queue>`` and ``t-<topic>`` (Terraform labels ``b_*``,
``q_*``, ``t_*``); infrastructure buckets such as the code-upload bucket use
other labels and are destroyed as usual.

On Azure the storage account, Service Bus namespace and resource group that
hold kept queues, containers or topics are kept too: destroying them would
delete their contents.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Tuple

# Terraform resource types that hold data, per provider.
DATA_TYPES: Dict[str, Tuple[str, ...]] = {
    "aws": ("aws_s3_bucket", "aws_s3_bucket_public_access_block", "aws_sqs_queue", "aws_sns_topic"),
    "gcp": ("google_storage_bucket", "google_pubsub_topic"),
    "azure": ("azurerm_storage_queue", "azurerm_storage_container", "azurerm_servicebus_topic"),
    "alibaba": ("alicloud_oss_bucket", "alicloud_message_service_topic", "alicloud_message_service_queue"),
    "tencent": ("tencentcloud_cos_bucket",),
    "huawei": ("huaweicloud_obs_bucket", "huaweicloud_smn_topic"),
    "oracle": ("oci_objectstorage_bucket",),
}
# Containers that must survive with what they hold (only when something is kept).
_PARENTS: Dict[str, Tuple[str, ...]] = {
    "azure": ("azurerm_storage_account", "azurerm_servicebus_namespace", "azurerm_resource_group"),
}
_DATA_LABEL = re.compile(r"^[bqt]_")
_ADDRESS = re.compile(r"^(?:module\.[^.]+\.)*([a-z0-9_]+)\.([A-Za-z0-9_-]+)(?:\[.*\])?$")


def kept_addresses(provider: str, state: Iterable[str]) -> List[str]:
    """The addresses from ``terraform state list`` that ``--keep-resources`` keeps."""
    types = DATA_TYPES.get(provider, ())
    kept: List[str] = []
    parents: List[str] = []
    for address in state:
        address = address.strip()
        match = _ADDRESS.match(address)
        if not match or address.startswith("data."):
            continue
        kind, label = match.groups()
        if kind in types and _DATA_LABEL.match(label):
            kept.append(address)
        elif kind in _PARENTS.get(provider, ()):
            parents.append(address)
    return kept + parents if kept else []


def supports(provider: str) -> bool:
    return provider in DATA_TYPES


__all__ = ["DATA_TYPES", "kept_addresses", "supports"]
