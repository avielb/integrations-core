# (C) Datadog, Inc. 2018
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
import traceback
import win32wnet
from six import iteritems

try:
    from .winpdh import WinPDHCounter, DATA_TYPE_INT, DATA_TYPE_DOUBLE
except ImportError:
    from .winpdh_stub import WinPDHCounter, DATA_TYPE_INT, DATA_TYPE_DOUBLE

from ... import AgentCheck, is_affirmative
from ...utils.containers import hash_mutable

int_types = [
    "int",
    "long",
    "uint",
]

double_types = [
    "double",
    "float",
]


class PDHBaseCheck(AgentCheck):
    """
    PDH based check.  check.

    Windows only.
    """
    def __init__(self, name, init_config, agentConfig, instances, counter_list):
        AgentCheck.__init__(self, name, init_config, agentConfig, instances)
        self._countersettypes = {}
        self._counters = {}
        self._missing_counters = {}
        self._metrics = {}
        self._tags = {}
        key = None

        try:
            for instance in instances:
                key = hash_mutable(instance)

                cfg_tags = instance.get('tags')
                if cfg_tags is not None:
                    if not isinstance(cfg_tags, list):
                        self.log.error("Tags must be configured as a list")
                        raise ValueError("Tags must be type list, not %s" % str(type(cfg_tags)))
                    self._tags[key] = list(cfg_tags)

                remote_machine = None
                host = instance.get('host')
                self._metrics[key] = []
                if host is not None and host != ".":
                    try:
                        remote_machine = host

                        username = instance.get('username')
                        password = instance.get('password')
                        nr = win32wnet.NETRESOURCE()
                        nr.lpRemoteName = r"\\%s\c$" % remote_machine
                        nr.dwType = 0
                        nr.lpLocalName = None
                        win32wnet.WNetAddConnection2(nr, password, username, 0)

                    except Exception as e:
                        self.log.error("Failed to make remote connection %s" % str(e))
                        return

                # counter_data_types allows the precision with which counters are queried
                # to be configured on a per-metric basis. In the metric instance, precision
                # should be specified as
                # counter_data_types:
                # - iis.httpd_request_method.get,int
                # - iis.net.bytes_rcvd,float
                #
                # the above would query the counter associated with iis.httpd_request_method.get
                # as an integer (LONG) and iis.net.bytes_rcvd as a double
                datatypes = {}
                precisions = instance.get('counter_data_types')
                if precisions is not None:
                    if not isinstance(precisions, list):
                        self.log.warning("incorrect type for counter_data_type %s" % str(precisions))
                    else:
                        for p in precisions:
                            k, v = p.split(",")
                            v = v.lower().strip()
                            if v in int_types:
                                self.log.info("Setting datatype for %s to integer" % k)
                                datatypes[k] = DATA_TYPE_INT
                            elif v in double_types:
                                self.log.info("Setting datatype for %s to double" % k)
                                datatypes[k] = DATA_TYPE_DOUBLE
                            else:
                                self.log.warning("Unknown data type %s" % str(v))

                self._make_counters(key, (counter_list, (datatypes, remote_machine, False, 'entry')))

                # get any additional metrics in the instance
                addl_metrics = instance.get('additional_metrics')
                if addl_metrics is not None:
                    self._make_counters(
                        key, (addl_metrics, (datatypes, remote_machine, True, 'additional metric entry'))
                    )

        except Exception as e:
            self.log.debug("Exception in PDH init: %s", str(e))
            raise

        if key is None or not self._metrics.get(key):
            raise AttributeError('No valid counters to collect')

    def check(self, instance):
        self.log.debug("PDHBaseCheck: check()")
        key = hash_mutable(instance)
        cache_counter_instances = is_affirmative(instance.get('cache_counter_instances', True))

        if not cache_counter_instances:
            for counter, values in list(iteritems(self._missing_counters)):
                self._make_counters(key, ([counter], values))

        for inst_name, dd_name, metric_func, counter in self._metrics[key]:
            try:
                if not cache_counter_instances:
                    counter.collect_counters()
                vals = counter.get_all_values()
                for instance_name, val in iteritems(vals):
                    tags = []
                    if key in self._tags:
                        tags = list(self._tags[key])

                    if not counter.is_single_instance():
                        tag = "instance:%s" % instance_name
                        tags.append(tag)
                    metric_func(dd_name, val, tags)
            except Exception as e:
                # don't give up on all of the metrics because one failed
                self.log.error(
                    "Failed to get data for %s %s: %s %s" % (inst_name, dd_name, str(e), traceback.format_exc())
                )

    def _make_counters(self, key, counter_data):
        counter_list, (datatypes, remote_machine, check_instance, message) = counter_data

        # list of the metrics. Each entry is itself an entry,
        # which is the pdh name, datadog metric name, type, and the
        # pdh counter object
        for counterset, inst_name, counter_name, dd_name, mtype in counter_list:
            if check_instance and self._no_instance(inst_name):
                inst_name = None

            m = getattr(self, mtype.lower())
            precision = datatypes.get(dd_name)

            try:
                obj = WinPDHCounter(
                    counterset,
                    counter_name,
                    self.log,
                    inst_name,
                    machine_name=remote_machine,
                    precision=precision
                )
            except Exception as e:
                self.log.warning('Could not create counter {}\\{} due to {}'.format(counterset, counter_name, e))
                self.log.warning('Datadog Agent will not report {}'.format(dd_name))
                self._missing_counters[(counterset, inst_name, counter_name, dd_name, mtype)] = (
                    datatypes, remote_machine, check_instance, message
                )
                continue
            else:
                self._missing_counters.pop((counterset, inst_name, counter_name, dd_name, mtype), None)

            entry = [inst_name, dd_name, m, obj]
            self.log.debug('{}: {}'.format(message, entry))
            self._metrics[key].append(entry)

    @classmethod
    def _no_instance(cls, inst_name):
        return (
            inst_name.lower() == 'none' or
            len(inst_name) == 0 or
            inst_name == '*' or
            inst_name.lower() == 'all'
        )
