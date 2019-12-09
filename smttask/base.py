import os
import logging
import abc
import inspect
from pathlib import Path
from attrdict import AttrDict
import numpy as np
from sumatra.projects import load_project
from sumatra.parameters import build_parameters
from mackelab_toolbox.parameters import params_to_lists
import mackelab_toolbox.iotools as io
logger = logging.getLogger()

from numbers import Number
from sumatra.parameters import NTParameterSet as ParameterSet
from sumatra.datastore.filesystem import DataFile
PlainArg = (Number, str)

__ALL__ = ['project', 'File', 'NotComputed', 'TaskBase', 'RecordedTaskBase']

###########
# If needed, there variables could be overwritten in a project script
# FIXME: what if smttask is loaded for two different projects ?
project = load_project()
# PlainArg
cache_runs = False  # Set to true to cache run() executions in memory
# Can also be overriden at the class level
###########

# Monkey patch AttrDict to allow access to attributes with unicode chars
def _valid_name(self, key):
    cls = type(self)
    return (
        isinstance(key, str) and
        key.isidentifier() and key[0] != '_' and  # This line changed
        not hasattr(cls, key)
    )
import attrdict.mixins
attrdict.mixins.Attr._valid_name = _valid_name

class File:
     """Use this to specify a dependency which is a filename."""
     def __init__(self, filename):
         self.filename = filename
     def __str__(self):
         return str(self.filename)
     def __repr__(self):
         return "File({})".format(self.filename)
     def desc(self, filename=None):
         if filename is None: filename = self.filename
         return ParameterSet({
             'type': 'File',
             'filename': filename
         })

class NotComputed:
    pass

def cast(value, totype):
    if isinstance(totype, tuple):
        for T in totype:
            try:
                r = cast(value, T)
            except (ValueError, TypeError):
                pass
            else:
                return r
        raise TypeError("Unable to cast {} to type {}"
                        .format(value, totype))
    else:
        return T(value)

class TaskBase(abc.ABC):
    """
    Task format:
    Use `Task` or `InMemoryTask` as base class

    class MyTask(Task):
        inputs = {'a': int, 'b': (str, float)}
        outputs = {'c': str}
        @staticmethod
        def _run(a, b):
            c = a*b
            return str(c)

    inputs: dict
        Dictionary of varname: type pairs. Types can be wrapped as a tuple.
        Inputs will (should?) be validated against these types.
        Certain types (like DataFile) are treated differently.
        TODO: document which types and how.
    outputs: dict | list
        Dictionary of varname: format. The type is passed on to `io.save()`
        and `io.load()` to determine the data format.
        `format` may be either a type or format name registered in
        `iotools.defined_formats` or `iotools._format_types`. (See the
        `mackelab_toolbox.iotools` docs for more information.)
        If you don't need to specify the output types, can also be a list.
        Not however that the order is important, so an unordered mapping
        like a set will lead to errors.

    _run:
        Method signature must match the parameters names defined by `inputs`.
        Since a task should be stateless, `_run()` should not depend on `self`
        and therefore can be defined as a statimethod.
    """
    cache = None

    @property
    @abc.abstractmethod
    def inputs(self):
        pass
    @property
    @abc.abstractmethod
    def outputs(self):
        """
        List of strings, corresponding to variable names.
        These names are appended to the task digest to create unique filenames.
        The order is important, so don't use e.g. a `set`.
        """
        pass
    @abc.abstractmethod
    def _run(self):
        """
        This is where subclasses place their code.
        Returned value must match the shape of self.outputs:
        Either a dict with keys matching the names in self.outputs, or a tuple
        of same length as self.outputs.
        """
        pass

    def __new__(cls, params=None, *args, **kwargs):
        if isinstance(params, cls):
            return params
        else:
            return super().__new__(cls)
    def __init__(self, params=None, *, reason=None, **taskinputs):
        """
        Parameters
        ----------
        params: ParameterSet-like
            ParameterSet, or something which can be cast to a ParameterSet
            (like a dict or filename). The result will be parsed for task
            arguments defined in `self.inputs`.
        reason: str
            Arbitrary string included in the Sumatra record for the run.
            Serves the same purpose as a version control commit message,
            and simarly essential.
        **taskinputs:
            Task parameters can also be specified as keyword arguments,
            and will override those in :param:params.
        """
        assert hasattr(self, 'inputs')
        if 'reason' in self.inputs:
            raise AssertionError(
                "A task cannot define an input named 'reason'.")
        if 'cache_result' in self.inputs:
            raise AssertionError(
                "A task cannot define an input named 'cache_result'.")
        if isinstance(params, type(self)):
            # Skip initializion of pre-existing instance (see __new__)
            assert hasattr(params, '_inputs')
            return
        if params is None:
            params = {}
        elif isinstance(params, str):
            params = build_parameters(params)
        elif isinstance(params, dict):
            params = ParameterSet(params)
        else:
            if len(self.inputs) == 1:
                # For tasks with only one input, don't require dict
                θname, θtype = next(iter(self.inputs.items()))
                if len(taskinputs) > 0:
                    raise TypeError(f"Argument given by name {θname} "
                                    "and position.")
                if not isinstance(taskinputs, θtype):
                    # Cast to correct type
                    taskinputs = cast(taskinputs, θtype)
                taskinputs = ParameterSet({θname: taskinputs})
            else:
                raise ValueError("`params` should be either a dictionary "
                                 "or a path to a parameter file, however it "
                                 "is of type {}.".format(type(params)))
        taskinputs = {**params, **taskinputs}
        sigparams = inspect.signature(self._run).parameters
        required_inputs = [p.name
                           for p in sigparams.values()
                           if p.default is inspect._empty]
        default_inputs  = {p.name: p.default
                           for p in sigparams.values()
                           if p.default is not inspect._empty}
        if type(self.__class__.__dict__['_run']) is not staticmethod:
            # instance and class methods already provide 'self' or 'cls'
            firstarg = required_inputs.pop(0)
            # Only allowing 'self' and 'cls' ensures we don't accidentally
            # remove true input arguments
            assert firstarg in ("self", "cls")
        if not all((p in taskinputs) for p in required_inputs):
            raise TypeError(
                "Missing required inputs '{}'."
                .format(set(required_inputs).difference(taskinputs)))
        # Add default inputs so they are recorded as task arguments
        taskinputs = {**default_inputs, **taskinputs}
        self.taskinputs = taskinputs
        self._loaded_inputs = None  # Where inputs are stored once loaded
        self.input_descs = self.get_input_descs()
        self._run_result = NotComputed
        # self.inputs = self.get_inputs
        # self.outputpaths = []

    @property
    def desc(self):
        descset = ParameterSet({
            'taskname': type(self).__qualname__,
            'inputs': describe(self.input_descs)
        })
        # for k, v in self.input_descs.items():
        #     descset['inputs'][k] = describe(v)
        return descset

    @property
    def input_files(self):
        # Also makes paths relative, in case they weren't already
        store = project.input_datastore
        return [os.path.relpath(Path(input.full_path).resolve(),store)
                for input in self.input_descs.values()
                if isinstance(input, DataFile)]

    def get_input_descs(self):
        """
        Compares :param:taskinputs with the class's `input` descriptor,
        and constructs the input object.
        This object is what is used to compute the task digest, and therefore
        must reflect any change which would change the task output.
        In particular, this means resolving all file paths, because if
        an input file differs (e.g. a symbolic link points somewhere new),
        than the task must be recomputed.
        """
        # All paths are relative to the input datastore
        inputstore = project.input_datastore
        outputstore = project.data_store
        inputs = AttrDict()
        for name, θtype in type(self).inputs.items():
            θ = self.taskinputs[name]
            if isinstance(θ, PlainArg):
                inputs[name] = θ
            elif isinstance(θ, File):
                # FIXME: Shouldn't this check θtype ?
                inputs[name] = self.get_abs_input_path(θ)
            # elif isinstance(θ, RecordedTaskBase):
            #     inputs[name] = θ
                # outputs = θ.outputpaths
                # assert isinstance(outputs, dict)
                # # if isinstance(outputs, dict):
                # for outname, output in outputs.items():
                #     outputpath = io.find_file(inputstore.root/output)
                #     if isinstance(outputpath, list):
                #         logger.warning("Multiple input files found: "
                #                        + str(outputpath))
                #         outputpath = outputpath[0]
                #     # Dereference links: links may change, so in the db record
                #     # we want to save paths to actual files
                #     # Typically these are files in the output datastore, but we
                #     # save paths relative to the *input* datastore.root,
                #     # because that's the root we use to execute the task.
                #     input = DataFile(outpath, inputstore)
                #     inputs[name][outname] = DataFile(
                #         os.path.relpath(Path(input.full_path).resolve(),
                #                         inputstore.root))
                # # else:
                # #     # Assume output is a single filename
                # #     outputpath = io.find_file(datastore.root/outputs)
                # #     if isinstance(outputpath, list):
                # #         logger.warning("Multiple input files found: "
                # #                        + str(outputpath))
                # #         outputpath = outputpath[0]
                # #     inputs[name] = DataFile(outputpath, datastore)
            else:
                # warnings.warn("Task was not tested on inputs of type {}. "
                #               "Please make sure task digests are unique "
                #               "and reproducible.".format(θtype))
                inputs[name] = θ
        return inputs

    def load_inputs(self):
        if self._loaded_inputs is None:
            self._loaded_inputs = \
                AttrDict({k: io.load(v.full_path) if isinstance(v, DataFile)
                             else v.run() if isinstance(v, TaskBase)
                             else v
                          for k,v in self.taskinputs.items()})
        return self._loaded_inputs

    def get_abs_input_path(path):
        # Dereference links: links may change, so in the db record
        # we want to save paths to actual files
        # Typically these are files in the output datastore, but we
        # save paths relative to the *input* datastore.root,
        # because that's the root we use to execute the task.
        input = DataFile(θ, inputstore)
        inputs[name] = DataFile(
            os.path.relpath(Path(input.full_path).resolve(),
                            inputstore.root),
            inputstore)


class RecordedTaskBase(TaskBase):
    """A task which is saved to disk and? recorded with Sumatra."""
    # TODO: Add missing requirements (e.g. write())
    @property
    @abc.abstractmethod
    def outputpaths(self):
        pass


#############################
# Description function
#############################

from warnings import warn
from collections import Iterable, Sequence, Mapping
import scipy as sp
import scipy.stats
import scipy.stats._multivariate as _mv
from numbers import Number

from parameters import ParameterSet as ParameterSetBase

dist_warning = """Task was not tested on inputs of type {}.
Descriptions of distribution tasks need to be
special-cased because they simply include the memory address; the
returned description is this not reproducible.
""".replace('\n', ' ')

def describe(v):
    """
    Provides a single method, `describe`, for producing a unique and
    reproducible description of a variable.
    Description of sequences is intentionally not a one-to-one. From the point
    of view of parameters, all sequences are the same: they are either sequences
    of values we iterate over, or arrays used in vector operations. Both these
    uses are supported by by `ndarray`, so we treate sequences as follows:
        - For description (this function), all sequences are converted lists.
          This has a clean and compact string representation which is compatible
          with JSON.
        - When interpreting saved parameters, all sequences (which should all
          be lists) are converted to `ndarray`.
    Similarly, all mappings are converted to `ParameterSet`.

    This function is essentially one big if-else statement.
    """
    if isinstance(v, PlainArg) or v is None:
        return v
    elif isinstance(v, Sequence):
        r = [describe(u) for u in v]
        # OK, so it seems that Sumatra is ok with lists of dicts, but I leave
        # this here in case I need it later. Goes with "arg" test for Mappings
        # if not all(isinstance(u, PlainArg+(list,)) for u in r):
        #     # Sumatra only supports (nested) lists of plain args
        #     # -> Convert the list into a ParameterSet
        #     r = ParameterSet({f'arg{i}': u for i,u in enumerate(r)})
        return r
    elif isinstance(v, np.ndarray):
        return v.tolist()
    elif isinstance(v, Mapping):  # Covers ParameterSetBase
        # I think Sumatra only supports strings as keys
        r = ParameterSet({str(k):describe(u) for k,u in v.items()})
        for k in r.keys():
            # if k[:3].lower() == "arg":
            #     warn(f"Mapping keys beginning with 'arg', such as {k}, "
            #          "are reserved by `smttask`.")
            #     break
            if k.lower() == "type":
                warn("The mapping key 'type' is reserved by Sumatra and will "
                     "prevent the web interface from displaying the "
                     "parameters.")
        return r
    elif isinstance(v, Iterable):
        warn(f"Attempting to describe an iterable of type {type(v)}. Only "
             "Sequences (list, tuple) and ndarrays are properly supported.")
        return v
    elif isinstance(v, TaskBase):
        return v.desc
    elif isinstance(v, type):
        s = repr(v)
        if '<locals>' in s:
            warn(f"Type {s} is dynamically generated and thus not reproducible.")
        return s
    elif isinstance(v, File):
        return v.desc()
    elif isinstance(v, DataFile):
        return File.desc(v.full_path)

    # scipy.stats Distribution types
    elif isinstance(v,
        (_mv.multi_rv_generic, _mv.multi_rv_frozen)):
        if isinstance(v, _mv.multivariate_normal_gen):
            return "multivariate_normal"
        elif isinstance(v, _mv.multivariate_normal_frozen):
            return f"multivariate_normal(mean={v.mean()}, cov={v.cov()})"
        else:
            warn(dist_warning.format(type(v)))
            return repr(v)
    elif isinstance(v, _mv.multi_rv_frozen):
        if isinstance(v, _mv.multivariate_normal_gen):
            return f"multivariate_normal)"
        else:
            warn(dist_warning.format(type(v)))
            return repr(v)

    else:
        warn("Task was not tested on inputs of type {}. "
             "Please make sure task digests are unique "
             "and reproducible.".format(type(v)))
        return repr(v)