# Licensed under the GPL: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
# For details: https://github.com/PyCQA/pylint/blob/master/COPYING

try:
    import isort.api

    HAS_ISORT_5 = True
except ImportError:  # isort < 5
    import isort

    HAS_ISORT_5 = False

import codecs
import os
import re
import sys
import textwrap
import tokenize

from astroid import Module, modutils

from pylint.constants import PY_EXTS

DEFAULT_LINE_LENGTH = 79


def normalize_text(text, line_len=DEFAULT_LINE_LENGTH, indent=""):
    """Wrap the text on the given line length."""
    return "\n".join(
        textwrap.wrap(
            text, width=line_len, initial_indent=indent, subsequent_indent=indent
        )
    )


def get_module_and_frameid(node):
    """return the module name and the frame id in the module"""
    frame = node.frame()
    module, obj = "", []
    while frame:
        if isinstance(frame, Module):
            module = frame.name
        else:
            obj.append(getattr(frame, "name", "<lambda>"))
        try:
            frame = frame.parent.frame()
        except AttributeError:
            frame = None
    obj.reverse()
    return module, ".".join(obj)


def get_rst_title(title, character):
    """Permit to get a title formatted as ReStructuredText test (underlined with a chosen character)."""
    return f"{title}\n{character * len(title)}\n"


def get_rst_section(section, options, doc=None):
    """format an options section using as a ReStructuredText formatted output"""
    result = ""
    if section:
        result += get_rst_title(section, "'")
    if doc:
        formatted_doc = normalize_text(doc)
        result += "%s\n\n" % formatted_doc
    for optname, optdict, value in options:
        help_opt = optdict.get("help")
        result += ":%s:\n" % optname
        if help_opt:
            formatted_help = normalize_text(help_opt, indent="  ")
            result += "%s\n" % formatted_help
        if value:
            value = str(_format_option_value(optdict, value))
            result += "\n  Default: ``%s``\n" % value.replace("`` ", "```` ``")
    return result


def safe_decode(line, encoding, *args, **kwargs):
    """return decoded line from encoding or decode with default encoding"""
    try:
        return line.decode(encoding or sys.getdefaultencoding(), *args, **kwargs)
    except LookupError:
        return line.decode(sys.getdefaultencoding(), *args, **kwargs)


def decoding_stream(stream, encoding, errors="strict"):
    try:
        reader_cls = codecs.getreader(encoding or sys.getdefaultencoding())
    except LookupError:
        reader_cls = codecs.getreader(sys.getdefaultencoding())
    return reader_cls(stream, errors)


def tokenize_module(module):
    with module.stream() as stream:
        readline = stream.readline
        return list(tokenize.tokenize(readline))


def _basename_in_blacklist_re(base_name, black_list_re):
    """Determines if the basename is matched in a regex blacklist

    :param str base_name: The basename of the file
    :param list black_list_re: A collection of regex patterns to match against.
        Successful matches are blacklisted.

    :returns: `True` if the basename is blacklisted, `False` otherwise.
    :rtype: bool
    """
    for file_pattern in black_list_re:
        if file_pattern.match(base_name):
            return True
    return False


def _modpath_from_file(filename, is_namespace, path=None):
    def _is_package_cb(path, parts):
        return modutils.check_modpath_has_init(path, parts) or is_namespace

    return modutils.modpath_from_file_with_callback(
        filename, path=path, is_package_cb=_is_package_cb
    )


def get_python_path(filepath):
    dirname = os.path.realpath(os.path.expanduser(filepath))
    if not os.path.isdir(dirname):
        dirname = os.path.dirname(dirname)
    while True:
        if not os.path.exists(os.path.join(dirname, "__init__.py")):
            return dirname
        old_dirname = dirname
        dirname = os.path.dirname(dirname)
        if old_dirname == dirname:
            return os.getcwd()
    return None


def expand_modules(files_or_modules, black_list, black_list_re, lint_all):
    """take a list of files/modules/packages and return the list of tuple
    (file, module name) which have to be actually checked
    """
    result = []
    errors = []
    path = sys.path.copy()

    for something in files_or_modules:
        if os.path.basename(something) in black_list:
            continue
        if _basename_in_blacklist_re(os.path.basename(something), black_list_re):
            continue

        module_path = get_python_path(something)
        additional_search_path = [".", module_path] + path
        if os.path.exists(something):
            # this is a file or a directory
            try:
                modname = ".".join(
                    modutils.modpath_from_file(something, path=additional_search_path)
                )
            except ImportError:
                modname = os.path.splitext(os.path.basename(something))[0]
            if os.path.isdir(something):
                filepath = os.path.join(something, "__init__.py")
            else:
                filepath = something
        else:
            # suppose it's a module or package
            modname = something
            try:
                filepath = modutils.file_from_modpath(
                    modname.split("."), path=additional_search_path
                )
                if filepath is None:
                    continue
            except (ImportError, SyntaxError) as ex:
                # The SyntaxError is a Python bug and should be
                # removed once we move away from imp.find_module: https://bugs.python.org/issue10588
                errors.append({"key": "fatal", "mod": modname, "ex": ex})
                continue

        filepath = os.path.normpath(filepath)
        modparts = (modname or something).split(".")

        try:
            spec = modutils.file_info_from_modpath(
                modparts, path=additional_search_path
            )
        except ImportError:
            # Might not be acceptable, don't crash.
            is_namespace = False
            is_directory = os.path.isdir(something)
        else:
            is_namespace = modutils.is_namespace(spec)
            is_directory = modutils.is_directory(spec)

        if not is_namespace and (os.path.isfile(filepath) or not lint_all):
            result.append(
                {
                    "path": filepath,
                    "name": modname,
                    "isarg": True,
                    "basepath": filepath,
                    "basename": modname,
                }
            )

        has_init = (
            not (modname.endswith(".__init__") or modname == "__init__")
            and os.path.basename(filepath) == "__init__.py"
        )
        if has_init or is_namespace or is_directory:
            for subfilepath in modutils.get_module_files(
                os.path.dirname(filepath),
                black_list,
                is_namespace or lint_all,
            ):
                if filepath == subfilepath:
                    continue
                if _basename_in_blacklist_re(
                    os.path.basename(subfilepath), black_list_re
                ):
                    continue

                modpath = _modpath_from_file(
                    subfilepath, is_namespace or lint_all, path=additional_search_path
                )
                submodname = ".".join(modpath)
                result.append(
                    {
                        "path": subfilepath,
                        "name": submodname,
                        "isarg": False,
                        "basepath": filepath,
                        "basename": modname,
                    }
                )
    return result, errors


def register_plugins(linter, directory):
    """load all module and package in the given directory, looking for a
    'register' function in each one, used to register pylint checkers
    """
    imported = {}
    for filename in os.listdir(directory):
        base, extension = os.path.splitext(filename)
        if base in imported or base == "__pycache__":
            continue
        if (
            extension in PY_EXTS
            and base != "__init__"
            or (
                not extension
                and os.path.isdir(os.path.join(directory, base))
                and not filename.startswith(".")
            )
        ):
            try:
                module = modutils.load_module_from_file(
                    os.path.join(directory, filename)
                )
            except ValueError:
                # empty module name (usually emacs auto-save files)
                continue
            except ImportError as exc:
                print(f"Problem importing module {filename}: {exc}", file=sys.stderr)
            else:
                if hasattr(module, "register"):
                    module.register(linter)
                    imported[base] = 1


def get_global_option(checker, option, default=None):
    """Retrieve an option defined by the given *checker* or
    by all known option providers.

    It will look in the list of all options providers
    until the given *option* will be found.
    If the option wasn't found, the *default* value will be returned.
    """
    # First, try in the given checker's config.
    # After that, look in the options providers.

    try:
        return getattr(checker.config, option.replace("-", "_"))
    except AttributeError:
        pass
    for provider in checker.linter.options_providers:
        for options in provider.options:
            if options[0] == option:
                return getattr(provider.config, option.replace("-", "_"))
    return default


def deprecated_option(
    shortname=None, opt_type=None, help_msg=None, deprecation_msg=None
):
    def _warn_deprecated(option, optname, *args):  # pylint: disable=unused-argument
        if deprecation_msg:
            sys.stderr.write(deprecation_msg % (optname,))

    option = {
        "help": help_msg,
        "hide": True,
        "type": opt_type,
        "action": "callback",
        "callback": _warn_deprecated,
        "deprecated": True,
    }
    if shortname:
        option["shortname"] = shortname
    return option


def _splitstrip(string, sep=","):
    """return a list of stripped string by splitting the string given as
    argument on `sep` (',' by default). Empty string are discarded.

    >>> _splitstrip('a, b, c   ,  4,,')
    ['a', 'b', 'c', '4']
    >>> _splitstrip('a')
    ['a']
    >>> _splitstrip('a,\nb,\nc,')
    ['a', 'b', 'c']

    :type string: str or unicode
    :param string: a csv line

    :type sep: str or unicode
    :param sep: field separator, default to the comma (',')

    :rtype: str or unicode
    :return: the unquoted string (or the input string if it wasn't quoted)
    """
    return [word.strip() for word in string.split(sep) if word.strip()]


def _unquote(string):
    """remove optional quotes (simple or double) from the string

    :type string: str or unicode
    :param string: an optionally quoted string

    :rtype: str or unicode
    :return: the unquoted string (or the input string if it wasn't quoted)
    """
    if not string:
        return string
    if string[0] in "\"'":
        string = string[1:]
    if string[-1] in "\"'":
        string = string[:-1]
    return string


def _check_csv(value):
    if isinstance(value, (list, tuple)):
        return value
    return _splitstrip(value)


def _comment(string):
    """return string as a comment"""
    lines = [line.strip() for line in string.splitlines()]
    return "# " + ("%s# " % os.linesep).join(lines)


def _format_option_value(optdict, value):
    """return the user input's value from a 'compiled' value"""
    if isinstance(value, (list, tuple)):
        value = ",".join(_format_option_value(optdict, item) for item in value)
    elif isinstance(value, dict):
        value = ",".join(f"{k}:{v}" for k, v in value.items())
    elif hasattr(value, "match"):  # optdict.get('type') == 'regexp'
        # compiled regexp
        value = value.pattern
    elif optdict.get("type") == "yn":
        value = "yes" if value else "no"
    elif isinstance(value, str) and value.isspace():
        value = "'%s'" % value
    return value


def format_section(stream, section, options, doc=None):
    """format an options section using the INI format"""
    if doc:
        print(_comment(doc), file=stream)
    print("[%s]" % section, file=stream)
    _ini_format(stream, options)


def _ini_format(stream, options):
    """format options using the INI format"""
    for optname, optdict, value in options:
        value = _format_option_value(optdict, value)
        help_opt = optdict.get("help")
        if help_opt:
            help_opt = normalize_text(help_opt, indent="# ")
            print(file=stream)
            print(help_opt, file=stream)
        else:
            print(file=stream)
        if value is None:
            print("#%s=" % optname, file=stream)
        else:
            value = str(value).strip()
            if re.match(r"^([\w-]+,)+[\w-]+$", str(value)):
                separator = "\n " + " " * len(optname)
                value = separator.join(x + "," for x in str(value).split(","))
                # remove trailing ',' from last element of the list
                value = value[:-1]
            print(f"{optname}={value}", file=stream)


class IsortDriver:
    """A wrapper around isort API that changed between versions 4 and 5."""

    def __init__(self, config):
        if HAS_ISORT_5:
            self.isort5_config = isort.api.Config(
                # There is not typo here. EXTRA_standard_library is
                # what most users want. The option has been named
                # KNOWN_standard_library for ages in pylint and we
                # don't want to break compatibility.
                extra_standard_library=config.known_standard_library,
                known_third_party=config.known_third_party,
            )
        else:
            self.isort4_obj = isort.SortImports(  # pylint: disable=no-member
                file_contents="",
                known_standard_library=config.known_standard_library,
                known_third_party=config.known_third_party,
            )

    def place_module(self, package):
        if HAS_ISORT_5:
            return isort.api.place_module(package, self.isort5_config)
        return self.isort4_obj.place_module(package)
