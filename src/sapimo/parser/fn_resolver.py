from pprint import pprint
from copy import deepcopy
from pathlib import Path

import yaml
from awscli.customizations.cloudformation.yamlhelper import yaml_parse

from sapimo.utils import setup_logger

logger = setup_logger(__file__)


class FnResolver:
    def __init__(self, filepath: Path, region="us-east-1"):
        self._refs = {}
        self._preprocess(filepath, region)
        self._root: Path = filepath.parent

    def _preprocess(self, filepath: Path, region: str):
        """ preprocess: this method is overridden from super class """

        id = "123456789012"
        dummy_params = {
            "AWS::AccountId": id,
            "AWS::Region": region,
            "AWS::NotificationARNs": ["arn1", "arn2", "arn3"],
            "AWS::NoValue": None,  # FIXME
            "AWS::Partition": "aws",
            "AWS::StackId": "arn:aws:cloudformation:"+region+":"+id +\
            ":stack/teststack/51af3dc0-da77-11e4-872e-1234567db123",
            "AWS::StackName": "teststack",
            "AWS::URLSuffix": "amazonaws.com"
        }
        self._arn_tmp = "arn:aws:lambda:"+region + ":"+id+":{0}:{1}"
        try:
            yaml_str = open(filepath).read()
            self._whole = yaml_parse(yaml_str)
            logger.info(f'yaml_dict:{self._whole}')
        except yaml.parser.ParserError as e:
            logger.exception("yaml parse error")
        except yaml.scanner.ScannerError as e:
            logger.exception("yaml scan error")

        self._mappings = self._whole.get("Mappings", {})
        self._conditions = self._whole.get("Conditions", {})
        dummy_params.update(self._whole.get("Parameters", {}))
        self._parameters = dummy_params
        self._resources = self._whole.get("Resources", {})

        # create "Ref" map
        for name, val in self._resources.items():
            self._refs[name] = self._get_ref_and_attr(name, val)

        # treat Fn and reflect global props
        self._mappings = self._treat(self._mappings)
        self._conditions = self._treat(self._conditions)
        self._parameters = self._treat(self._parameters)
        self._resources = self._treat(self._resources)
        self._whole = self._treat(self._whole)

    def _get_ref_and_attr(self, name: str, resource: dict):
        """ for override """
        return {"Ref": name, "Arn": self._arn_tmp.format("other", name)}

    def _treat(self, dic: dict):
        """
            treat and replace Function parts (Func::*)
            and OrderedDict to dict
        """
        if isinstance(dic, list):
            res = []
            for elm in dic:
                res.append(self._treat(elm))
            return res
        elif not isinstance(dic, dict):
            return dic
        res = {}
        for key, val in dic.items():
            key = key.strip()
            if key == "Ref":
                res = self._treat(self._refs.get(val, {}).get("Ref", ""))
                if not res:
                    res = self._treat(self._parameters.get(val, ""))
            # elif key == "Condition": # ignore
            elif key.startswith("Fn::"):
                fn = key[4:]
                if fn == "GetAtt":
                    att = self._treat(val)
                    res = self._refs.get(val[0], {}).get(att[1], "")
                    # print(f"att={att}")
                    # print(f"res={res}")
                elif fn == "FindInMap":
                    map_name = self._treat(val[0])
                    k1 = self._treat(val[1])
                    k2 = self._treat(val[2])
                    res = self._mappings.get(
                        map_name, {}).get(k1, {}).get(k2, "")
                elif fn == "GetAZs":
                    res = ["us-east-1a", "us-east-1b"]  # dummy
                elif fn == "ImportValue":
                    res = self._treat(val)  # dummy
                elif fn == "Join":
                    res = val[0].join([self._treat(v) for v in val[1]])
                elif fn == "Select":
                    index = self._treat(val[0])
                    li = [self._treat(v) for v in val[1]]
                    res = li[index]
                elif fn == "Split":
                    res = self._treat(val[1]).split(val[0])
                elif fn == "Sub":
                    if isinstance(val, str):
                        res_str = val
                        val_map = self._parameters
                    else:
                        res_str: str = val[0]
                        val_map = self._treat(val[1])

                    for k, v in val_map.items():
                        old = "${"+k+"}"
                        if old in res_str:
                            res_str = res_str.replace(old, v)
                    res = res_str
                else:
                    res[key] = self._treat(val)
            else:
                res[key] = self._treat(val)
        return res
