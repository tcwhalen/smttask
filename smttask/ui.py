import click
import logging
from .base import Task, config
import os
from pathlib import Path
import sumatra.commands

@click.group()
def cli():
    pass

@cli.command()
def init():
    """Run an initialization wizard for SumatraTask.

    If you want to include this in a script, the underlying `smt init`
    command provided by Sumatra may be more appropriate.
    """
    print(
        "\nIn order to ensure task results are never overwritten, while still "
        "providing consistent file locations inputs, SumatraTask requires "
        "separate paths for output and input datastores (files in the 'input' directory "
        "are symlinked to their most recent equivalent in the 'output' "
        "directory). By default these are the subfolders './data' and "
        "'./data/run_dump'."
        )

    cwd = Path(os.getcwd())
    path_repo = cwd

    print("\nThe project directory should be a git repository containing "
          "the code and settings used for the project. "
          "Note: providing a different value will clone the repository at "
          "that location into the current directory.")
    r = input(f"Project directory (default {path_repo}): ")
    if r != "":
        path_repo = Path(r).expanduser()
        print(f"{path_repo} will be cloned to {cwd}.")

    project_name = cwd.stem
    r = input(f"Project name (default {project_name}): ")
    if r != "":
        project_name = r

    path_inputs = cwd/"data"
    path_outputs = cwd/"data/run_dump"
    r = input(f"\nInput datastore (default: {path_inputs}): ")
    if r != "":
        path_inputs = Path(r).expanduser()
    r = input(f"\nOutput datastore (default: {path_outputs}): ")
    if r != "":
        path_outputs = Path(r).expanduser()

    fail = False
    if path_inputs.exists() and not path_inputs.is_dir():
        print("Input datastore is not a directory.")
        fail = True
    if path_outputs.exists() and not path_outputs.is_dir():
        print("Output datastore is not a directory.")
        fail = True
    if path_inputs == path_outputs:
        print("Input and output datastore must be different.")
        fail = True
    # Check that input/output dirs are read-writable.
    # Dirs may not be created yet, so we go up the path until we find the
    # first already existing directory
    for path in (path_inputs, path_outputs):
        latest_parent = path
        while not latest_parent.exists():
            latest_parent = latest_parent.parent
        if not os.access(latest_parent, os.W_OK | os.R_OK):
            print(f"{latest_parent} is not read-writable")
            fail = True
    if fail:
        return

    # Construct the argument list as it would be passed on the CLI, and
    # call Sumatra's init
    argv_str = f"{project_name} --datapath {path_outputs} --input {path_inputs} " \
               f"--repository {path_repo}"
    print("This will initialize a Sumatra project with the following command:")
    print("smt init " + argv_str)
    r = input("Continue ? [Y/n] ")
    if r.lower() != "n":
        sumatra.commands.init(argv_str.split())

@cli.command()
@click.argument('taskdesc', type=click.File('r'))
@click.option('--record/--no-record', default=True,
    help="Use `--no-record` to disable recording (and thereby also the check "
         "that the version control repository is clean).")
@click.option('-v', '--verbose', count=True,
    help="Specify up to 3 times (`-vvv`) to increase the logging level which "
         "is printed. Default is to print only warning and error messages.\n"
         "default: warning and up (error, critical)\n"
         "-v: info and up\n-vv: debug and up\n-vvv: everything.")
@click.option('-q', '--quiet', count=True,
    help="Turn off warning messages. Specifying multiple times will also "
         "turn off error and critical messages.")
@click.option('--debug/--no-debug', default=False,
    help="Launch the debugger before running task.")
def run(taskdesc, record, verbose, quiet, debug):
    """Execute the Task defined in TASKDESC.

    A taskdesc can be obtained by calling `.desc.save()` on an
    instantiated task."
    """
    verbose *= 10; quiet *= 10  # Logging levels are in steps of 10
    default = logging.WARNING
    loglevel = max(min(default+quiet-verbose,
                       logging.CRITICAL),
                   logging.DEBUG)
    logging.basicConfig(level=loglevel)
    config.record = record
    task = Task.load(taskdesc)
    taskdesc.close()
    if debug:
        import pdb; pdb.set_trace()
        pass
    task.run()

if __name__ == "__main__":
    cli()
