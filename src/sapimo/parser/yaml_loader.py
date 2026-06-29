from collections import OrderedDict
import json

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode


class CloudFormationLoader(yaml.SafeLoader):
    """YAML loader for CloudFormation/SAM intrinsic function tags."""


def _construct_ordered_mapping(loader: yaml.SafeLoader, node: MappingNode):
    loader.flatten_mapping(node)
    return OrderedDict(loader.construct_pairs(node))


def _construct_intrinsic_function(loader, tag_prefix, node):
    del tag_prefix

    tag_name = node.tag[1:]
    intrinsic_name = tag_name if tag_name in {"Ref", "Condition"} else f"Fn::{tag_name}"

    if tag_name == "GetAtt" and isinstance(node, ScalarNode):
        value = loader.construct_scalar(node).split(".", 1)
    elif isinstance(node, ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)

    return {intrinsic_name: value}


CloudFormationLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_ordered_mapping,
)
CloudFormationLoader.add_multi_constructor("!", _construct_intrinsic_function)


def yaml_parse(yaml_text: str):
    """Parse JSON/YAML text while preserving CloudFormation intrinsic functions."""
    try:
        return json.loads(yaml_text, object_pairs_hook=OrderedDict)
    except json.JSONDecodeError:
        return yaml.load(yaml_text, Loader=CloudFormationLoader)
