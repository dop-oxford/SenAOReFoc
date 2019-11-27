# This file was automatically generated by SWIG (http://www.swig.org).
# Version 4.0.0
#
# Do not make changes to this file unless you know what you are doing--modify
# the SWIG interface file instead.

"""Alpao SDK module"""

from sys import version_info as _swig_python_version_info
if _swig_python_version_info < (2, 7, 0):
    raise RuntimeError('Python 2.7 or later required')

# Import the low-level C/C++ module
if __package__ or '.' in __name__:
    from . import _asdk
else:
    import _asdk

try:
    import builtins as __builtin__
except ImportError:
    import __builtin__

def _swig_setattr_nondynamic(self, class_type, name, value, static=1):
    if name == "thisown":
        return self.this.own(value)
    if name == "this":
        if type(value).__name__ == 'SwigPyObject':
            self.__dict__[name] = value
            return
    method = class_type.__swig_setmethods__.get(name, None)
    if method:
        return method(self, value)
    if not static:
        object.__setattr__(self, name, value)
    else:
        raise AttributeError("You cannot add attributes to %s" % self)


def _swig_setattr(self, class_type, name, value):
    return _swig_setattr_nondynamic(self, class_type, name, value, 0)


def _swig_getattr(self, class_type, name):
    if name == "thisown":
        return self.this.own()
    method = class_type.__swig_getmethods__.get(name, None)
    if method:
        return method(self)
    raise AttributeError("'%s' object has no attribute '%s'" % (class_type.__name__, name))


def _swig_repr(self):
    try:
        strthis = "proxy of " + self.this.__repr__()
    except __builtin__.Exception:
        strthis = ""
    return "<%s.%s; %s >" % (self.__class__.__module__, self.__class__.__name__, strthis,)


def _swig_setattr_nondynamic_instance_variable(set):
    def set_instance_attr(self, name, value):
        if name == "thisown":
            self.this.own(value)
        elif name == "this":
            set(self, name, value)
        elif hasattr(self, name) and isinstance(getattr(type(self), name), property):
            set(self, name, value)
        else:
            raise AttributeError("You cannot add instance attributes to %s" % self)
    return set_instance_attr


def _swig_setattr_nondynamic_class_variable(set):
    def set_class_attr(cls, name, value):
        if hasattr(cls, name) and not isinstance(getattr(cls, name), property):
            set(cls, name, value)
        else:
            raise AttributeError("You cannot add class attributes to %s" % cls)
    return set_class_attr


def _swig_add_metaclass(metaclass):
    """Class decorator for adding a metaclass to a SWIG wrapped class - a slimmed down version of six.add_metaclass"""
    def wrapper(cls):
        return metaclass(cls.__name__, cls.__bases__, cls.__dict__.copy())
    return wrapper


class _SwigNonDynamicMeta(type):
    """Meta class to enforce nondynamic attributes (no new attributes) for a class"""
    __setattr__ = _swig_setattr_nondynamic_class_variable(type.__setattr__)


class DM(object):
    r"""Proxy of C++ acs::DM class."""

    thisown = property(lambda x: x.this.own(), lambda x, v: x.this.own(v), doc='The membership flag')
    __repr__ = _swig_repr

    def __init__(self, serialNumber):
        r"""__init__(DM self, acs::CStrConst serialNumber) -> DM"""
        _asdk.DM_swiginit(self, _asdk.new_DM(serialNumber))
    __swig_destroy__ = _asdk.delete_DM

    def Reset(self):
        r"""Reset(DM self) -> acs::COMPL_STAT"""
        return _asdk.DM_Reset(self)

    def Stop(self):
        r"""Stop(DM self) -> acs::COMPL_STAT"""
        return _asdk.DM_Stop(self)

    def Send(self, *args):
        r"""
        Send(DM self, acs::Scalar const * values) -> acs::COMPL_STAT
        Send(DM self, acs::Scalar const * values, acs::UInt nPattern, acs::UInt nRepeat=1) -> acs::COMPL_STAT
        """
        return _asdk.DM_Send(self, *args)

    def SendOne(self, arg2, arg3, arg4, arg5):
        r"""SendOne(DM self, acs::Scalar const * arg2, acs::UInt arg3, acs::UInt arg4, acs::UInt arg5) -> acs::COMPL_STAT"""
        return _asdk.DM_SendOne(self, arg2, arg3, arg4, arg5)

    def Get(self, command):
        r"""Get(DM self, acs::CStrConst command) -> acs::Scalar"""
        return _asdk.DM_Get(self, command)

    def Set(self, *args):
        r"""
        Set(DM self, acs::CStrConst command, acs::Scalar value)
        Set(DM self, acs::CStrConst command, acs::Int value)
        Set(DM self, acs::CStrConst command, acs::CStrConst str)
        """
        return _asdk.DM_Set(self, *args)

    @staticmethod
    def Check():
        r"""Check() -> acs::Bool"""
        return _asdk.DM_Check()

    @staticmethod
    def GetLastError():
        r"""GetLastError() -> acs::UInt"""
        return _asdk.DM_GetLastError()

    def __str__(self):
        r"""__str__(DM self) -> char *"""
        return _asdk.DM___str__(self)

# Register DM in _asdk:
_asdk.DM_swigregister(DM)

def DM_Check():
    r"""DM_Check() -> acs::Bool"""
    return _asdk.DM_Check()

def DM_GetLastError():
    r"""DM_GetLastError() -> acs::UInt"""
    return _asdk.DM_GetLastError()


def __lshift__(arg1, arg2):
    r"""__lshift__(std::ostream & arg1, DM arg2) -> std::ostream &"""
    return _asdk.__lshift__(arg1, arg2)

