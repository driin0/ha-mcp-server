"""get_addon - credentials in add-on options must not reach the caller.

Two leaks seen on a real instance with 2.4.0, both on options the redaction
never looked at:

- the SSH add-on keeps its password one level down, in
  `options.ssh.password`, and only top-level option names were checked;
- an add-on kept an enrollment token inside a URL in a free-text option
  (`install_command: "curl https://host/install?enroll_token=..."`), whose
  *name* says nothing about credentials.

The values below are invented; none of them is a real credential.
"""
import pytest

PLANTED = "s3cr3t-PLANTED-value"


@pytest.fixture
def addon(fake_ha, monkeypatch):
    from tools import addons

    monkeypatch.setattr(addons, "_SUPERVISOR_BASE", "http://supervisor")

    def serve(options):
        fake_ha.rest_responses["/addons/x_test/info"] = (200, {"data": {
            "slug": "x_test", "name": "Test", "options": options,
        }})
        return addons.get_addon("x_test")["options"]

    return serve


def test_a_password_nested_in_an_option_object_is_redacted(addon):
    options = addon({
        "ssh": {"username": "hassio", "password": PLANTED,
                "authorized_keys": ["ssh-ed25519 AAAA public"]},
        "zsh": True,
    })

    assert options["ssh"]["password"] == "<redacted>"
    # What is not a credential stays readable.
    assert options["ssh"]["username"] == "hassio"
    assert options["ssh"]["authorized_keys"] == ["ssh-ed25519 AAAA public"]
    assert options["zsh"] is True


def test_credentials_inside_lists_of_objects_are_redacted(addon):
    # Mosquitto's shape: a list of login objects.
    options = addon({"logins": [{"username": "u1", "password": PLANTED},
                                {"username": "u2", "Password": PLANTED}]})

    assert [l["password" if "password" in l else "Password"]
            for l in options["logins"]] == ["<redacted>", "<redacted>"]
    assert [l["username"] for l in options["logins"]] == ["u1", "u2"]


def test_a_credential_named_option_holding_an_object_is_redacted_whole(addon):
    options = addon({"credentials": {"user": "u", "secret": PLANTED}})

    assert options["credentials"] == "<redacted>"


def test_a_token_in_a_url_query_string_is_redacted(addon):
    options = addon({
        "install_command":
            f"curl -fsSL 'https://agent.example/install.sh?os=linux&enroll_token={PLANTED}&v=2' | sh",
    })

    cmd = options["install_command"]
    assert PLANTED not in cmd
    assert "enroll_token=<redacted>" in cmd
    # The rest of the command is still there to be read.
    assert "os=linux" in cmd and "&v=2" in cmd


@pytest.mark.parametrize("template", [
    "https://h/p?api_key={s}",
    "https://h/p?x=1&Secret={s}#frag",
    "https://h/p?access_token={s}",
    "https://h/p?password={s}",
    "https://user:{s}@host.example/path",
    "Authorization: Bearer {s}",
    "authorization: basic {s}",
    "agent --token {s} --verbose",
    "agent --api-key={s}",
    "TOKEN={s} ./run.sh",
])
def test_credential_shapes_inside_free_text_are_redacted(addon, template):
    options = addon({"command": template.format(s=PLANTED)})

    assert PLANTED not in options["command"]
    assert "<redacted>" in options["command"]


@pytest.mark.parametrize("text", [
    "https://h/p?keyword=lights&page=2",
    "https://user@host.example/path",
    "--monkey business",
    "alexa_keywords=kitchen",
])
def test_ordinary_text_is_left_alone(addon, text):
    assert addon({"command": text})["command"] == text


def test_an_empty_nested_credential_still_reads_as_unset(addon):
    options = addon({"ssh": {"password": ""}})

    assert options["ssh"]["password"] == ""


def test_the_caller_input_is_not_mutated(fake_ha, monkeypatch):
    from tools import addons

    monkeypatch.setattr(addons, "_SUPERVISOR_BASE", "http://supervisor")
    original = {"ssh": {"password": PLANTED}}
    addons._redact_options(original)

    assert original["ssh"]["password"] == PLANTED
