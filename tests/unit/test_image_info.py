from sapimo.parser.image_info import ImageInfo
import pytest
from pathlib import Path
import os

from sapimo.exceptions import DockerFileParseError


# method


def test_split_space():
    src = "ENV MY_NAME=\"Nori Asa\" MY_DOG=Rex\\ The\\ Dog   \nMY_CAT=fluffy"
    expected = ["ENV",
                "MY_NAME=\"Nori Asa\"",
                "MY_DOG=Rex The Dog",
                "MY_CAT=fluffy"]
    assert ImageInfo._split_space(src) == expected


def test_read_list_str():
    src = "a b[] c\n de"
    expected = ["a", "b[]", "c", "de"]
    assert ImageInfo._read_list_str(src) == expected


def test_read_list():
    src = "['a', \"b[]\", 'c\n', de]"
    expected = ["a", "b[]", "c", "de"]
    assert ImageInfo._read_list_str(src) == expected

# class


@pytest.fixture
def get_target_obj():
    def _get_target_obj():
        meta = {
            "Dockerfile": "app/Dockerfile",
            "DockerContext": ".",
            "DockerTag": "python3.9-v1"
        }
        root = Path(__file__).parent / "simple_image"
        return ImageInfo(metadata=meta, root=root)
    return _get_target_obj


@pytest.fixture
def create_dockerfile():
    docker_file_path = Path(__file__).parent / \
        "simple_image" / "app" / "Dockerfile"
    temp_files = []

    def _create_dockerfile(lines: list[str], files=None, cmd=None):
        commands = [
            "FROM public.ecr.aws/lambda/python:3.9",
            "RUN python3.9 -m pip install -r requirements.txt -t ."
        ]
        commands += lines
        commands.append(cmd or "CMD [\"app.lambda_handler\"]")

        with open(docker_file_path, "w") as f:
            for l in commands:
                f.write(l + "\n")
        add_files = ["app.py", "requirements.txt"]
        if files:
            add_files += files

        for file in add_files:
            file_path: Path = docker_file_path.parent / file
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.touch()
            temp_files.append(file_path)

    yield _create_dockerfile
    docker_file_path.unlink()
    for file in temp_files:
        file.unlink()


def test_read_env(get_target_obj, create_dockerfile):
    create_dockerfile([
        "COPY app/app.py app/requirements.txt ./",
        "ENV \"MY_NAME\"=\"Nori Asa\" MY_DOG=Rex\\ The\\ Dog   \\\nMY_CAT=fluffy",
        "ENV VERSION 1.0",
        "ENV GROUP=test=group"])
    obj: ImageInfo = get_target_obj()
    expected = {"MY_NAME": "Nori Asa",
                "MY_DOG": "Rex The Dog",
                "MY_CAT": "fluffy",
                "VERSION": "1.0",
                "GROUP": "test=group"}
    print("expected:")
    print(expected)
    print("actual:")
    print(obj.envs)
    for k in expected.keys():
        assert expected[k] == obj.envs[k]


def test_read_handler(get_target_obj, create_dockerfile):
    create_dockerfile([
        "WORKDIR /work",
        "COPY app/app.py app/requirements.txt ./",
        "COPY app/model /opt/ml/model"
    ], cmd="CMD app.my_lambda_handler")
    obj: ImageInfo = get_target_obj()
    assert obj.handler == "app.my_lambda_handler"
    assert obj.code_uri == "app/"


def test_read_copy_wildcard(get_target_obj, create_dockerfile):
    """ wild card : is this different from filepath.Match of Go?"""
    create_dockerfile(["COPY app/aa*.py ./", "COPY app/app.py ./"],
                      files=["aa1.py", "aa22.py", "bb/aab.py", "aa3.txt"])
    obj: ImageInfo = get_target_obj()
    files = [p.name for p in obj._copies.values()]

    assert "aa1.py" in files
    assert "aa22.py" in files
    assert "aab.py" not in files  # ?


def test_add_libs(get_target_obj, create_dockerfile):
    """ treat some libs as layer """
    create_dockerfile([
        "COPY app/app.py app/requirements.txt ./",
        "COPY share/libs ./"
    ], files=["../share/libs/mylib.py"])
    obj: ImageInfo = get_target_obj()
    assert obj.layers == ["share/libs/"]


def test_add_libs_on_dir(get_target_obj, create_dockerfile):
    """ treat some libs as layer """
    create_dockerfile([
        "COPY app/app.py app/requirements.txt ./",
        "COPY share/libs ./libs/"
    ], files=["../share/libs/mylib.py"])
    obj: ImageInfo = get_target_obj()
    assert obj.layers == ["share/"]


def test_error_add_libs(get_target_obj, create_dockerfile):
    """ treat some libs as layer """
    create_dockerfile([
        "COPY app/app.py app/requirements.txt ./",
        "COPY share/libs ./dockerdir/"
    ], files=["../share/libs/mylib.py"])
    with pytest.raises(DockerFileParseError):
        obj: ImageInfo = get_target_obj()
