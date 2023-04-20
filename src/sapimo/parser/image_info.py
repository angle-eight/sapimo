from pathlib import Path
import os

from sapimo.utils import LogManager
from sapimo.exceptions import DockerFileParseError

logger = LogManager.setup_logger(__file__)


class ImageInfo:
    def __init__(self, metadata: dict, root: Path):
        doc_context = self._resolve_path(root, metadata["DockerContext"])
        doc_file = self._resolve_path(doc_context, metadata["Dockerfile"])
        doc_tag: str = metadata["DockerTag"]
        self._context_root: Path = doc_context
        self._root = root
        self.envs = {
            "LAMBDA_TASK_ROOT": "/var/task",
            "LAMBDA_RUNTIME_DIR": "/var/runtime"
        }
        self.handler = ""
        self.code_uri = ""
        self._copies = {}
        self.layers = []
        self._work_dir = Path("/")

        self._read_docker_file(doc_file)

    def _add_copies(self, args: list[str]):
        args = self._read_list_str(" ".join(args))

        srcs = args[:-1]
        dst = args[-1]
        dst_is_dir = dst.endswith("/")
        if not dst.startswith("/"):
            dst = self._work_dir / dst
        else:
            dst = Path(dst)
        for src in srcs:
            if src.startswith("--chown"):
                continue
            if src.startswith("--from"):
                msg = "sapimo: syntax not supported"
                raise DockerFileParseError(msg)
            # not support filename which contains special char([etc)
            # only * and ? are supported
            if "*" in src or "?" in src:
                for s in self._context_root.glob(src):
                    dst_str = str(dst/s.name)  # if dst_is_dir else str(dst)
                    self._copies[dst_str] = s
            else:
                s_path = self._context_root / src
                if s_path.is_file():
                    d_str = str(dst / s_path.name) if dst_is_dir else str(dst)
                    self._copies[d_str] = s_path
                elif s_path.is_dir():
                    rel_path = s_path.relative_to(self._context_root)
                    self._add_copies([str(rel_path)+"/*",
                                      str(dst)])

    def _read_docker_file(self, file: Path):
        with open(file, "r")as f:
            lines = f.readlines()

        pre = ""
        handler = ""
        for line in lines:
            line = line.strip()
            if line.startswith("#"):
                continue
            elif line.endswith("\\"):
                pre = pre + line[:-1]
                continue
            line = pre+line
            pre = ""
            cmd, *args = self._split_space(line)
            cmd = cmd.upper()
            if cmd == "COPY":
                self._add_copies(args)
            elif cmd == "ENV":
                next_key = ""
                for elm in args:
                    if next_key:
                        self.envs[self._trim_dq(next_key)] = self._trim_dq(elm)
                        next_key = ""
                    elif "=" in elm:
                        k, v = elm.split("=", 1)
                        self.envs[self._trim_dq(k)] = self._trim_dq(v)
                    else:
                        next_key = elm

            elif cmd == "ADD":
                msg = "sapimo: 'ADD' command (in dockerfile)"\
                    "is not supported at sapimo."\
                    "this command is ignored.\n"\
                    "(if possible, use 'COPY' instead of 'ADD')"
                logger.warning(msg)
            elif cmd == "CMD":
                opts = self._read_list_str("".join(args))
                handler = opts[0]  # ignore options
                handler = handler.replace('"', '')
            elif cmd == "ENTRYPOINT" or cmd == "ARG":
                msg = f"sapimo: '{cmd}' command (in dockerfile)"\
                    "is not supported at sapimo."\
                    "this command is ignored.\n"
                logger.warning(msg)
            elif cmd == "WORKDIR":
                path = args[0]
                if path.startswith("/"):
                    self._work_dir = Path(path)
                else:
                    self._work_dir = self._work_dir / path
            else:
                # "RUN" etc. are ignored, maybe its no problem
                pass
        if not handler:
            raise DockerFileParseError("lambda handler not found")

        self.handler = handler
        entry_py = "/".join(handler.split(".")[:-1]) + ".py"
        logger.info(f"entry file={entry_py}")
        print(entry_py)
        for dst, src in self._copies.items():
            print(dst)
            if dst.endswith(entry_py):
                src_entry_point = src
                dst_entry_root = Path(dst).parent
                break
        else:
            raise DockerFileParseError("entry point not found")
        relative = str(src_entry_point.relative_to(self._root))
        self.code_uri = relative.replace(entry_py, "")

        layers = set()
        for dst, src in self._copies.items():
            dst = Path(dst)
            if dst_entry_root in dst.parents:
                relative = str(dst.relative_to(dst_entry_root))
                if relative == entry_py:
                    continue
                if relative in str(src):
                    layer_path = str(src).replace(relative, "")
                    layer = layer_path.replace(str(self._root), "")
                    layer = layer[1:]  # remove head "/"
                    if layer != self.code_uri:
                        layers.add((layer))
                else:
                    msg = "changed directory tree by container building.\n"\
                        "this case is not supported yet at sapimo"
                    raise DockerFileParseError(msg)
        self.layers = list(layers)

    def _resolve_env_val(self, line: str) -> str:
        if "$" in line:
            for k, v in self.envs.items():
                line = line.replace("$"+k, v)
        return line

    @staticmethod
    def _resolve_path(root: Path, path: str) -> Path:
        if path.startswith("/"):
            res = Path(path)
        else:
            res: Path = root / path
        return res.resolve()

    @staticmethod
    def _read_list_str(list_str: str) -> list[str]:
        """
            str obj "['a', 'b', 'c']" -> list obj ["a", "b", "c"]
            str obj "a b c" -> list obj ["a", "b", "c"]
            nested list is not supported
        """
        list_str = list_str.strip()
        list_str = list_str.replace("'", '"')
        if list_str.startswith("[") and list_str.endswith("]"):
            string = list_str[1:-1]
            elms = [s.replace('"', '').replace('"', '').strip()
                    for s in string.split(",")]
        else:
            elms = ImageInfo._split_space(list_str)
        return elms

    @staticmethod
    def _split_space(base: str) -> list[str]:
        """
            - split at space, but not split in " ",
            - remove empty element
            - remove \n from every element
        """
        res = []
        in_q = False
        escaping = False
        sep = 0
        for i, s in enumerate(base):
            if escaping:
                escaping = False
            elif s == '"':
                in_q = not in_q
            elif not in_q and s == " ":
                word = base[sep:i].replace("\\", "").strip()
                if word:
                    res.append(word)
                sep = i+1
            elif s == "\\":
                escaping = True
            else:
                pass
        else:
            word = base[sep:].replace("\\", "").strip()
            if word:
                res.append(word)
        while len(res) < 2:
            res.append("")
        return res

    @staticmethod
    def _trim_dq(s: str):
        s = s.strip()
        if s.startswith("\"") and s.endswith("\""):
            return s[1:-1]
        else:
            return s
