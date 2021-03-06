#!/usr/bin/python
# -*- encoding: utf-8; py-indent-offset: 4 -*-
# +------------------------------------------------------------------+
# |             ____ _               _        __  __ _  __           |
# |            / ___| |__   ___  ___| | __   |  \/  | |/ /           |
# |           | |   | '_ \ / _ \/ __| |/ /   | |\/| | ' /            |
# |           | |___| | | |  __/ (__|   <    | |  | | . \            |
# |            \____|_| |_|\___|\___|_|\_\___|_|  |_|_|\_\           |
# |                                                                  |
# | Copyright Mathias Kettner 2014             mk@mathias-kettner.de |
# +------------------------------------------------------------------+
#
# This file is part of Check_MK.
# The official homepage is at http://mathias-kettner.de/check_mk.
#
# check_mk is free software;  you can redistribute it and/or modify it
# under the  terms of the  GNU General Public License  as published by
# the Free Software Foundation in version 2.  check_mk is  distributed
# in the hope that it will be useful, but WITHOUT ANY WARRANTY;  with-
# out even the implied warranty of  MERCHANTABILITY  or  FITNESS FOR A
# PARTICULAR PURPOSE. See the  GNU General Public License for more de-
# ails.  You should have  received  a copy of the  GNU  General Public
# License along with GNU Make; see the file  COPYING.  If  not,  write
# to the Free Software Foundation, Inc., 51 Franklin St,  Fifth Floor,
# Boston, MA 02110-1301 USA.

# This file is being used by the rule based notifications and (CEE
# only) by the alert handling

import pprint, urllib, select, subprocess, socket

def event_keepalive(event_function, log_function, call_every_loop=None, loop_interval=None, shutdown_function=None):
    last_config_timestamp = config_timestamp()

    # Send signal that we are ready to receive the next event, but
    # not after a config-reload-restart (see below)
    if os.getenv("CMK_EVENT_RESTART") != "1":
        log_function("Starting in keepalive mode with PID %d" % os.getpid())
        sys.stdout.write("*")
        sys.stdout.flush()
    else:
        log_function("We are back after a restart.")

    while True:
        try:
            # Invalidate timeperiod cache
            global g_inactive_timerperiods
            g_inactive_timerperiods = None

            # If the configuration has changed, we do a restart. But we do
            # this check just before the next event arrives. We must
            # *not* read data from stdin, just peek! There is still one
            # problem: when restarting we must *not* send the initial '*'
            # byte, because that must be not no sooner then the events
            # has been sent. We do this by setting the environment variable
            # CMK_EVENT_RESTART=1

            if event_data_available(loop_interval):
                if last_config_timestamp != config_timestamp():
                    log_function("Configuration has changed. Restarting myself.")
                    if shutdown_function:
                        shutdown_function()
                    os.putenv("CMK_EVENT_RESTART", "1")
                    os.execvp("cmk", sys.argv)

                data = ""
                while not data.endswith("\n\n"):
                    try:
                        new_data = ""
                        new_data = os.read(0, 32768)
                    except IOError, e:
                        new_data = ""
                    except Exception, e:
                        if opt_debug:
                            raise
                        log_function("Cannot read data from CMC: %s" % e)

                    if not new_data:
                        log_function("CMC has closed the connection. Shutting down.")
                        if shutdown_function:
                            shutdown_function()
                        sys.exit(0) # closed stdin, this is
                    data += new_data

                try:
                    context = raw_context_from_string(data.rstrip('\n'))
                    event_function(context)
                except Exception, e:
                    if opt_debug:
                        raise
                    log_function("ERROR %s\n%s" % (e, format_exception()))

                # Signal that we are ready for the next event
                sys.stdout.write("*")
                sys.stdout.flush()


	# Fix vor Python 2.4:
        except SystemExit, e:
            sys.exit(e)
        except Exception, e:
            if opt_debug:
                raise
            log_function("ERROR %s\n%s" % (e, format_exception()))

        if call_every_loop:
            try:
                call_every_loop()
            except Exception, e:
                if opt_debug:
                    raise
                log_function("ERROR %s\n%s" % (e, format_exception()))


def event_data_available(loop_interval):
    readable, writeable, exceptionable = select.select([0], [], [], loop_interval)
    return not not readable


def raw_context_from_string(data):
    # Context is line-by-line in g_notify_readahead_buffer
    context = {}
    try:
        for line in data.split('\n'):
            varname, value = line.strip().split("=", 1)
            context[varname] = expand_backslashes(value)
    except Exception, e: # line without '=' ignored or alerted
        if opt_debug:
            raise
    return context

def raw_context_from_stdin():
    context = {}
    for line in sys.stdin:
        varname, value = line.strip().split("=", 1)
        context[varname] = expand_backslashes(value)
    return context


def expand_backslashes(value):
    # We cannot do the following:
    # value.replace(r"\n", "\n").replace("\\\\", "\\")
    # \\n would be exapnded to \<LF> instead of \n. This was a bug
    # in previous versions.
    return value.replace("\\\\", "\0").replace("\\n", "\n").replace("\0", "\\")


def convert_context_to_unicode(context):
    # Convert all values to unicode
    for key, value in context.iteritems():
        if type(value) == str:
            try:
                value_unicode = value.decode("utf-8")
            except:
                try:
                    value_unicode = value.decode("latin-1")
                except:
                    value_unicode = u"(Invalid byte sequence)"
            context[key] = value_unicode


def render_context_dump(raw_context):
    encoded_context = dict(raw_context.items())
    convert_context_to_unicode(encoded_context)
    return "Raw context:\n" \
               + "\n".join(["                    %s=%s" % v for v in sorted(encoded_context.items())])


def event_log(logfile_path, message):
    formatted = u"%s %s\n" % (time.strftime("%F %T", time.localtime()), message)
    file(logfile_path, "a").write(formatted.encode("utf-8"))


def find_host_service_in_context(context):
    host = context.get("HOSTNAME", "UNKNOWN")
    service = context.get("SERVICEDESC")
    if service:
        return host + ";" + service
    else:
        return host

# Add a few further helper variables that are useful in notification and alert plugins
def complete_raw_context(raw_context, with_dump, event_log):
    raw_keys = list(raw_context.keys())

    try:
        raw_context["WHAT"] = raw_context.get("SERVICEDESC") and "SERVICE" or "HOST"
        raw_context["MONITORING_HOST"] = socket.gethostname()
        raw_context["LOGDIR"] = notification_logdir
        if omd_root:
            raw_context["OMD_ROOT"] = omd_root
            raw_context["OMD_SITE"] = os.getenv("OMD_SITE", "")
        raw_context["MAIL_COMMAND"] = notification_mail_command

        # The Check_MK Micro Core sends the MICROTIME and no other time stamps. We add
        # a few Nagios-like variants in order to be compatible
        if "MICROTIME" in raw_context:
            microtime = int(raw_context["MICROTIME"])
            timestamp = float(microtime) / 1000000.0
            broken = time.localtime(timestamp)
            raw_context["DATE"] = time.strftime("%Y-%m-%d", broken)
            raw_context["SHORTDATETIME"] = time.strftime("%Y-%m-%d %H:%M:%S", broken)
            raw_context["LONGDATETIME"] = time.strftime("%a %b %d %H:%M:%S %Z %Y", broken)

        raw_context['HOSTURL'] = '/check_mk/index.py?start_url=%s' % \
                            urlencode('view.py?view_name=hoststatus&host=%s' % raw_context['HOSTNAME'])
        if raw_context['WHAT'] == 'SERVICE':
            raw_context['SERVICEURL'] = '/check_mk/index.py?start_url=%s' % \
                                        urlencode('view.py?view_name=service&host=%s&service=%s' %
                                                     (raw_context['HOSTNAME'], raw_context['SERVICEDESC']))

        # Relative Timestamps for several macros
        for macro in [ 'LASTHOSTSTATECHANGE', 'LASTSERVICESTATECHANGE', 'LASTHOSTUP', 'LASTSERVICEOK' ]:
            if macro in raw_context:
                raw_context[macro + '_REL'] = get_readable_rel_date(raw_context[macro])


        # Rule based notifications enabled? We might need to complete a few macros
        contact = raw_context.get("CONTACTNAME")
        if not contact or contact == "check-mk-notify":
            add_rulebased_macros(raw_context)

        # For custom notifications the number is set to 0 by the core (Nagios and CMC). We force at least
        # number 1 here, so that rules with conditions on numbers do not fail (the minimum is 1 here)
        for what in [ "HOST", "SERVICE" ]:
            key = what + "NOTIFICATIONNUMBER"
            if key in raw_context and  raw_context[key] == "0":
                raw_context[key] = "1"

        # Add the previous hard state. This is neccessary for notification rules that depend on certain transitions,
        # like OK -> WARN (but not CRIT -> WARN). The CMC sends PREVIOUSHOSTHARDSTATE and PREVIOUSSERVICEHARDSTATE.
        # Nagios does not have this information and we try to deduct this.
        if "PREVIOUSHOSTHARDSTATE" not in raw_context and "LASTHOSTSTATE" in raw_context:
            prev_state = raw_context["LASTHOSTSTATE"]
            # When the attempts are > 1 then the last state could be identical with
            # the current one, e.g. both critical. In that case we assume the
            # previous hard state to be OK.
            if prev_state == raw_context["HOSTSTATE"]:
                prev_state = "UP"
            elif "HOSTATTEMPT" not in raw_context or \
                ("HOSTATTEMPT" in raw_context and raw_context["HOSTATTEMPT"] != "1"):
                # Here We do not know. The transition might be OK -> WARN -> CRIT and
                # the initial OK is completely lost. We use the artificial state "?"
                # here, which matches all states and makes sure that when in doubt a
                # notification is being sent out. But when the new state is UP, then
                # we know that the previous state was a hard state (otherwise there
                # would not have been any notification)
                if raw_context["HOSTSTATE"] != "UP":
                    prev_state = "?"
                event_log("Previous host hard state not known. Allowing all states.")
            raw_context["PREVIOUSHOSTHARDSTATE"] = prev_state

        # Same for services
        if raw_context["WHAT"] == "SERVICE" and "PREVIOUSSERVICEHARDSTATE" not in raw_context:
            prev_state = raw_context["LASTSERVICESTATE"]
            if prev_state == raw_context["SERVICESTATE"]:
                prev_state = "OK"
            elif "SERVICEATTEMPT" not in raw_context or \
                ("SERVICEATTEMPT" in raw_context and raw_context["SERVICEATTEMPT"] != "1"):
                if raw_context["SERVICESTATE"] != "OK":
                    prev_state = "?"
                event_log("Previous service hard state not known. Allowing all states.")
            raw_context["PREVIOUSSERVICEHARDSTATE"] = prev_state

        # Add short variants for state names (at most 4 characters)
        for key, value in raw_context.items():
            if key.endswith("STATE"):
                raw_context[key[:-5] + "SHORTSTATE"] = value[:4]

        if raw_context["WHAT"] == "SERVICE":
            raw_context['SERVICEFORURL'] = urllib.quote(raw_context['SERVICEDESC'])
        raw_context['HOSTFORURL'] = urllib.quote(raw_context['HOSTNAME'])

        convert_context_to_unicode(raw_context)

    except Exception, e:
        event_log("Error on completing raw context: %s" % e)

    if with_dump:
        event_log("Computed variables:\n"
                   + "\n".join(sorted(["                    %s=%s" % (k, raw_context[k]) for k in raw_context if k not in raw_keys])))



def event_match_rule(rule, context):
    return \
        event_match_folder(rule, context)                or \
        event_match_hosttags(rule, context)              or \
        event_match_hostgroups(rule, context)            or \
        event_match_servicegroups(rule, context)         or \
        event_match_contacts(rule, context)              or \
        event_match_contactgroups(rule, context)         or \
        event_match_hosts(rule, context)                 or \
        event_match_exclude_hosts(rule, context)         or \
        event_match_services(rule, context)              or \
        event_match_exclude_services(rule, context)      or \
        event_match_plugin_output(rule, context)         or \
        event_match_checktype(rule, context)             or \
        event_match_timeperiod(rule)                     or \
        event_match_servicelevel(rule, context)


def event_match_folder(rule, context):
    if "match_folder" in rule:
        mustfolder = rule["match_folder"]
        mustpath = mustfolder.split("/")
        hasfolder = None
        for tag in context.get("HOSTTAGS", "").split():
            if tag.startswith("/wato/"):
                hasfolder = tag[6:].rstrip("/")
                haspath = hasfolder.split("/")
                if mustpath == ["",]:
                    return # Match is on main folder, always OK
                while mustpath:
                    if not haspath or mustpath[0] != haspath[0]:
                        return "The rule requires WATO folder '%s', but the host is in '%s'" % (
                            mustfolder, hasfolder)
                    mustpath = mustpath[1:]
                    haspath = haspath[1:]

        if hasfolder == None:
            return "The host is not managed via WATO, but the rule requires a WATO folder"


def event_match_hosttags(rule, context):
    required = rule.get("match_hosttags")
    if required:
        tags = context.get("HOSTTAGS", "").split()
        if not hosttags_match_taglist(tags, required):
            return "The host's tags %s do not match the required tags %s" % (
                "|".join(tags), "|".join(required))


def event_match_servicegroups(rule, context):
    if context["WHAT"] != "SERVICE":
        return
    required_groups = rule.get("match_servicegroups")
    if required_groups != None:
        sgn = context.get("SERVICEGROUPNAMES")
        if sgn == None:
            return "No information about service groups is in the context, but service " \
                   "must be in group %s" % ( " or ".join(required_groups))
        if sgn:
            servicegroups = sgn.split(",")
        else:
            return "The service is in no group, but %s is required" % (
                 " or ".join(required_groups))

        for group in required_groups:
            if group in servicegroups:
                return

        return "The service is only in the groups %s, but %s is required" % (
              sgn, " or ".join(required_groups))

def event_match_contacts(rule, context):
    if "match_contacts" in rule:
        required_contacts = rule["match_contacts"]
        contacts_text = context["CONTACTS"]
        if not contacts_text:
            return "The object has no contact, but %s is required" % (
                 " or ".join(required_contacts))

        contacts = contacts_text.split(",")
        for contact in required_contacts:
            if contact in contacts:
                return

        return "The object has the contacts %s, but %s is required" % (
              contacts_text, " or ".join(required_contacts))


def event_match_contactgroups(rule, context):
    required_groups = rule.get("match_contactgroups")
    if context["WHAT"] == "SERVICE":
        cgn = context.get("SERVICECONTACTGROUPNAMES")
    else:
        cgn = context.get("HOSTCONTACTGROUPNAMES")

    if required_groups != None:
        if cgn == None:
            notify_log("Warning: No information about contact groups in the context. " \
                       "Seams that you don't use the Check_MK Microcore. ")
            return
        if cgn:
            contactgroups = cgn.split(",")
        else:
            return "The object is in no group, but %s is required" % (
                 " or ".join(required_groups))

        for group in required_groups:
            if group in contactgroups:
                return

        return "The object is only in the groups %s, but %s is required" % (
              cgn, " or ".join(required_groups))


def event_match_hostgroups(rule, context):
    required_groups = rule.get("match_hostgroups")
    if required_groups != None:
        hgn = context.get("HOSTGROUPNAMES")
        if hgn == None:
            return "No information about host groups is in the context, but host " \
                   "must be in group %s" % ( " or ".join(required_groups))
        if hgn:
            hostgroups = hgn.split(",")
        else:
            return "The host is in no group, but %s is required" % (
                 " or ".join(required_groups))

        for group in required_groups:
            if group in hostgroups:
                return

        return "The host is only in the groups %s, but %s is required" % (
              hgn, " or ".join(required_groups))


def event_match_hosts(rule, context):
    if "match_hosts" in rule:
        hostlist = rule["match_hosts"]
        if context["HOSTNAME"] not in hostlist:
            return "The host's name '%s' is not on the list of allowed hosts (%s)" % (
                context["HOSTNAME"], ", ".join(hostlist))


def event_match_exclude_hosts(rule, context):
    if context["HOSTNAME"] in rule.get("match_exclude_hosts", []):
        return "The host's name '%s' is on the list of excluded hosts" % context["HOSTNAME"]


def event_match_services(rule, context):
    if "match_services" in rule:
        if context["WHAT"] != "SERVICE":
            return "The rule specifies a list of services, but this is a host notification."
        servicelist = rule["match_services"]
        service = context["SERVICEDESC"]
        if not in_extraconf_servicelist(servicelist, service):
            return "The service's description '%s' dows not match by the list of " \
                   "allowed services (%s)" % (service, ", ".join(servicelist))


def event_match_exclude_services(rule, context):
    if context["WHAT"] != "SERVICE":
        return
    excludelist = rule.get("match_exclude_services", [])
    service = context["SERVICEDESC"]
    if in_extraconf_servicelist(excludelist, service):
        return "The service's description '%s' matches the list of excluded services" \
          % context["SERVICEDESC"]


def event_match_plugin_output(rule, context):
    if "match_plugin_output" in rule:
        r = regex(rule["match_plugin_output"])

        if context["WHAT"] == "SERVICE":
            output = context["SERVICEOUTPUT"]
        else:
            output = context["HOSTOUTPUT"]
        if not r.search(output):
            return "The expression '%s' cannot be found in the plugin output '%s'" % \
                (rule["match_plugin_output"], output)


def event_match_checktype(rule, context):
    if "match_checktype" in rule:
        if context["WHAT"] != "SERVICE":
            return "The rule specifies a list of Check_MK plugins, but this is a host notification."
        command = context["SERVICECHECKCOMMAND"]
        if not command.startswith("check_mk-"):
            return "The rule specified a list of Check_MK plugins, but his is no Check_MK service."
        plugin = command[9:]
        allowed = rule["match_checktype"]
        if plugin not in allowed:
            return "The Check_MK plugin '%s' is not on the list of allowed plugins (%s)" % \
              (plugin, ", ".join(allowed))


def event_match_timeperiod(rule):
    if "match_timeperiod" in rule:
        timeperiod = rule["match_timeperiod"]
        if timeperiod != "24X7" and not check_timeperiod(timeperiod):
            return "The timeperiod '%s' is currently not active." % timeperiod


def event_match_servicelevel(rule, context):
    if "match_sl" in rule:
        from_sl, to_sl = rule["match_sl"]
        if context['WHAT'] == "SERVICE" and context.get('SVC_SL','').isdigit():
            sl = saveint(context.get('SVC_SL'))
        else:
            sl = saveint(context.get('HOST_SL'))

        if sl < from_sl or sl > to_sl:
            return "The service level %d is not between %d and %d." % (sl, from_sl, to_sl)


def add_context_to_environment(plugin_context, prefix):
    for key in plugin_context:
        os.putenv(prefix + key, plugin_context[key].encode('utf-8'))

def remove_context_from_environment(plugin_context, prefix):
    for key in plugin_context:
        os.unsetenv(prefix + key)

