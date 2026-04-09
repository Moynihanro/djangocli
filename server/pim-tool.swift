import Foundation
import EventKit
import Contacts

// MARK: - Output Helpers

func output(_ value: Any) {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
       let str = String(data: data, encoding: .utf8) {
        print(str)
    } else {
        fputs("Error: Failed to serialize output\n", stderr)
        exit(1)
    }
}

func fail(_ message: String, code: Int32 = 1) -> Never {
    fputs("Error: \(message)\n", stderr)
    exit(code)
}

// MARK: - Date Helpers

let isoFormatter: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    return f
}()

let isoLocalFormatter: DateFormatter = {
    let f = DateFormatter()
    f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
    f.timeZone = TimeZone.current
    return f
}()

let displayFormatter: DateFormatter = {
    let f = DateFormatter()
    f.dateFormat = "EEEE, MMMM d, yyyy 'at' h:mm:ss a"
    f.timeZone = TimeZone.current
    return f
}()

func parseDate(_ str: String) -> Date? {
    // Try ISO 8601 with timezone first
    if let d = isoFormatter.date(from: str) { return d }
    // Try ISO 8601 local (no timezone)
    if let d = isoLocalFormatter.date(from: str) { return d }
    // Try display format ("Monday, April 7, 2026 at 5:00:00 PM")
    if let d = displayFormatter.date(from: str) { return d }
    // Try without day name
    let shortDisplay = DateFormatter()
    shortDisplay.dateFormat = "MMMM d, yyyy 'at' h:mm:ss a"
    shortDisplay.timeZone = TimeZone.current
    if let d = shortDisplay.date(from: str) { return d }
    // Try date only
    let dateOnly = DateFormatter()
    dateOnly.dateFormat = "yyyy-MM-dd"
    dateOnly.timeZone = TimeZone.current
    if let d = dateOnly.date(from: str) { return d }
    return nil
}

func formatDate(_ date: Date) -> String {
    return isoLocalFormatter.string(from: date)
}

func formatDisplay(_ date: Date) -> String {
    return displayFormatter.string(from: date)
}

func parseJSON(_ str: String) -> [String: Any] {
    guard let data = str.data(using: .utf8),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        fail("Invalid JSON input")
    }
    return obj
}

// MARK: - EventKit Store (shared)

let eventStore = EKEventStore()

func authorizeCalendar() {
    let sem = DispatchSemaphore(value: 0)
    var granted = false
    if #available(macOS 14.0, *) {
        eventStore.requestFullAccessToEvents { g, _ in
            granted = g
            sem.signal()
        }
    } else {
        eventStore.requestAccess(to: .event) { g, _ in
            granted = g
            sem.signal()
        }
    }
    sem.wait()
    if !granted { fail("Calendar access denied. Grant permission in System Settings > Privacy & Security > Calendars.", code: 2) }
}

func authorizeReminders() {
    let sem = DispatchSemaphore(value: 0)
    var granted = false
    if #available(macOS 14.0, *) {
        eventStore.requestFullAccessToReminders { g, _ in
            granted = g
            sem.signal()
        }
    } else {
        eventStore.requestAccess(to: .reminder) { g, _ in
            granted = g
            sem.signal()
        }
    }
    sem.wait()
    if !granted { fail("Reminders access denied. Grant permission in System Settings > Privacy & Security > Reminders.", code: 2) }
}

// MARK: - Calendar Commands

func calendarToday() {
    authorizeCalendar()
    let cal = Calendar.current
    let start = cal.startOfDay(for: Date())
    let end = cal.date(byAdding: .day, value: 1, to: start)!
    let predicate = eventStore.predicateForEvents(withStart: start, end: end, calendars: nil)
    let events = eventStore.events(matching: predicate)
    let result = events.map { evt -> [String: Any] in
        return [
            "summary": evt.title ?? "",
            "start": formatDate(evt.startDate),
            "end": formatDate(evt.endDate),
            "start_display": formatDisplay(evt.startDate),
            "end_display": formatDisplay(evt.endDate),
            "calendar": evt.calendar.title,
            "all_day": evt.isAllDay
        ]
    }
    output(["events": result])
}

func calendarRange(_ days: Int) {
    authorizeCalendar()
    let cal = Calendar.current
    let start = cal.startOfDay(for: Date())
    let end = cal.date(byAdding: .day, value: days, to: start)!
    let predicate = eventStore.predicateForEvents(withStart: start, end: end, calendars: nil)
    let events = eventStore.events(matching: predicate)
    let result = events.map { evt -> [String: Any] in
        return [
            "summary": evt.title ?? "",
            "start": formatDate(evt.startDate),
            "end": formatDate(evt.endDate),
            "start_display": formatDisplay(evt.startDate),
            "end_display": formatDisplay(evt.endDate),
            "calendar": evt.calendar.title,
            "all_day": evt.isAllDay
        ]
    }
    output(["days": days, "events": result])
}

func calendarCreate(_ jsonStr: String) {
    authorizeCalendar()
    let json = parseJSON(jsonStr)
    guard let summary = json["summary"] as? String,
          let startStr = json["start"] as? String,
          let endStr = json["end"] as? String else {
        fail("Missing required fields: summary, start, end")
    }
    guard let startDate = parseDate(startStr) else { fail("Invalid start date: \(startStr)") }
    guard let endDate = parseDate(endStr) else { fail("Invalid end date: \(endStr)") }

    let calName = json["calendar_name"] as? String ?? "Home"
    let targetCal = eventStore.calendars(for: .event).first { $0.title.lowercased() == calName.lowercased() }
        ?? eventStore.defaultCalendarForNewEvents

    guard let cal = targetCal else { fail("No calendar found") }

    let event = EKEvent(eventStore: eventStore)
    event.title = summary
    event.startDate = startDate
    event.endDate = endDate
    event.calendar = cal

    do {
        try eventStore.save(event, span: .thisEvent)
        output(["status": "ok", "summary": summary])
    } catch {
        fail("Failed to create event: \(error.localizedDescription)")
    }
}

func calendarEdit(_ jsonStr: String) {
    authorizeCalendar()
    let json = parseJSON(jsonStr)
    guard let originalSummary = json["original_summary"] as? String else {
        fail("Missing required field: original_summary")
    }

    // Search wide range for matching event
    let cal = Calendar.current
    let start = cal.date(byAdding: .day, value: -90, to: Date())!
    let end = cal.date(byAdding: .day, value: 365, to: Date())!
    let predicate = eventStore.predicateForEvents(withStart: start, end: end, calendars: nil)
    let events = eventStore.events(matching: predicate)

    guard let event = events.first(where: { $0.title == originalSummary }) else {
        fail("Event '\(originalSummary)' not found", code: 3)
    }

    if let newSummary = json["new_summary"] as? String {
        event.title = newSummary
    }
    if let newStartStr = json["new_start"] as? String, let newStart = parseDate(newStartStr) {
        event.startDate = newStart
    }
    if let newEndStr = json["new_end"] as? String, let newEnd = parseDate(newEndStr) {
        event.endDate = newEnd
    }

    do {
        try eventStore.save(event, span: .thisEvent)
        output(["status": "ok", "original": originalSummary])
    } catch {
        fail("Failed to edit event: \(error.localizedDescription)")
    }
}

func calendarDelete(_ summary: String) {
    authorizeCalendar()
    let cal = Calendar.current
    let start = cal.date(byAdding: .day, value: -90, to: Date())!
    let end = cal.date(byAdding: .day, value: 365, to: Date())!
    let predicate = eventStore.predicateForEvents(withStart: start, end: end, calendars: nil)
    let events = eventStore.events(matching: predicate)

    guard let event = events.first(where: { $0.title == summary }) else {
        fail("Event '\(summary)' not found", code: 3)
    }

    do {
        try eventStore.remove(event, span: .thisEvent)
        output(["status": "ok", "deleted": summary])
    } catch {
        fail("Failed to delete event: \(error.localizedDescription)")
    }
}

// MARK: - Reminders Commands

func remindersList() {
    authorizeReminders()
    let predicate = eventStore.predicateForIncompleteReminders(withDueDateStarting: nil, ending: nil, calendars: nil)
    let sem = DispatchSemaphore(value: 0)
    var fetched: [EKReminder]?
    eventStore.fetchReminders(matching: predicate) { reminders in
        fetched = reminders
        sem.signal()
    }
    sem.wait()

    let result = (fetched ?? []).map { r -> [String: Any] in
        var dict: [String: Any] = [
            "name": r.title ?? "",
            "list": r.calendar.title
        ]
        if let due = r.dueDateComponents, let date = Calendar.current.date(from: due) {
            dict["due"] = formatDate(date)
            dict["due_display"] = formatDisplay(date)
        } else {
            dict["due"] = ""
            dict["due_display"] = ""
        }
        return dict
    }
    output(["reminders": result])
}

func remindersCreate(_ jsonStr: String) {
    authorizeReminders()
    let json = parseJSON(jsonStr)
    guard let name = json["name"] as? String else { fail("Missing required field: name") }

    let listName = json["list_name"] as? String ?? "Reminders"
    let matchedList = eventStore.calendars(for: .reminder).first { $0.title.lowercased() == listName.lowercased() }
    let targetList = matchedList ?? eventStore.defaultCalendarForNewReminders()

    guard let list = targetList else { fail("No reminder list found") }

    let reminder = EKReminder(eventStore: eventStore)
    reminder.title = name
    reminder.calendar = list

    if let dueStr = json["due"] as? String, !dueStr.isEmpty, let dueDate = parseDate(dueStr) {
        reminder.dueDateComponents = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute, .second], from: dueDate)
        // Also add an alarm at the due date
        reminder.addAlarm(EKAlarm(absoluteDate: dueDate))
    }

    do {
        try eventStore.save(reminder, commit: true)
        output(["status": "ok", "name": name])
    } catch {
        fail("Failed to create reminder: \(error.localizedDescription)")
    }
}

func remindersComplete(_ name: String) {
    authorizeReminders()
    let predicate = eventStore.predicateForIncompleteReminders(withDueDateStarting: nil, ending: nil, calendars: nil)
    let sem = DispatchSemaphore(value: 0)
    var fetched: [EKReminder]?
    eventStore.fetchReminders(matching: predicate) { reminders in
        fetched = reminders
        sem.signal()
    }
    sem.wait()

    let searchName = name.lowercased()
    guard let reminder = (fetched ?? []).first(where: { ($0.title ?? "").lowercased().contains(searchName) }) else {
        fail("Reminder '\(name)' not found", code: 3)
    }

    reminder.isCompleted = true
    do {
        try eventStore.save(reminder, commit: true)
        output(["status": "ok", "completed": reminder.title ?? name])
    } catch {
        fail("Failed to complete reminder: \(error.localizedDescription)")
    }
}

// MARK: - Contacts Commands

func contactsSearch(_ name: String) {
    let store = CNContactStore()
    let sem = DispatchSemaphore(value: 0)
    var granted = false
    store.requestAccess(for: .contacts) { g, _ in
        granted = g
        sem.signal()
    }
    sem.wait()
    if !granted { fail("Contacts access denied. Grant permission in System Settings > Privacy & Security > Contacts.", code: 2) }

    let keys: [CNKeyDescriptor] = [
        CNContactGivenNameKey as CNKeyDescriptor,
        CNContactFamilyNameKey as CNKeyDescriptor,
        CNContactPhoneNumbersKey as CNKeyDescriptor,
        CNContactEmailAddressesKey as CNKeyDescriptor
    ]

    do {
        let predicate = CNContact.predicateForContacts(matchingName: name)
        let contacts = try store.unifiedContacts(matching: predicate, keysToFetch: keys)
        let result = contacts.map { c -> [String: Any] in
            let fullName = "\(c.givenName) \(c.familyName)".trimmingCharacters(in: .whitespaces)
            let phones = c.phoneNumbers.map { $0.value.stringValue }.joined(separator: " ")
            let emails = c.emailAddresses.map { $0.value as String }.joined(separator: " ")
            return ["name": fullName, "phones": phones, "emails": emails]
        }
        output(["query": name, "contacts": result])
    } catch {
        fail("Contact search failed: \(error.localizedDescription)")
    }
}

func contactsReverse(_ phone: String) {
    let store = CNContactStore()
    let sem = DispatchSemaphore(value: 0)
    var granted = false
    store.requestAccess(for: .contacts) { g, _ in
        granted = g
        sem.signal()
    }
    sem.wait()
    if !granted { fail("Contacts access denied.", code: 2) }

    let keys: [CNKeyDescriptor] = [
        CNContactGivenNameKey as CNKeyDescriptor,
        CNContactFamilyNameKey as CNKeyDescriptor,
        CNContactPhoneNumbersKey as CNKeyDescriptor,
        CNContactEmailAddressesKey as CNKeyDescriptor
    ]

    // CNContact.predicateForContacts(matching:) requires CNPhoneNumber
    let phoneNumber = CNPhoneNumber(stringValue: phone)
    do {
        let predicate = CNContact.predicateForContacts(matching: phoneNumber)
        let contacts = try store.unifiedContacts(matching: predicate, keysToFetch: keys)
        if let c = contacts.first {
            let fullName = "\(c.givenName) \(c.familyName)".trimmingCharacters(in: .whitespaces)
            let phones = c.phoneNumbers.map { $0.value.stringValue }.joined(separator: " ")
            let emails = c.emailAddresses.map { $0.value as String }.joined(separator: " ")
            output(["phone": phone, "name": fullName, "phones": phones, "emails": emails])
        } else {
            output(["phone": phone, "name": phone, "phones": "", "emails": ""])
        }
    } catch {
        fail("Reverse lookup failed: \(error.localizedDescription)")
    }
}

// MARK: - Main

let args = CommandLine.arguments
guard args.count >= 3 else {
    fputs("Usage: pim-tool <service> <command> [args...]\n", stderr)
    fputs("\n", stderr)
    fputs("Services:\n", stderr)
    fputs("  calendar today                     List today's events\n", stderr)
    fputs("  calendar range <days>              List events for next N days\n", stderr)
    fputs("  calendar create '<json>'           Create event\n", stderr)
    fputs("  calendar edit '<json>'             Edit event\n", stderr)
    fputs("  calendar delete '<summary>'        Delete event\n", stderr)
    fputs("  reminders list                     List incomplete reminders\n", stderr)
    fputs("  reminders create '<json>'          Create reminder\n", stderr)
    fputs("  reminders complete '<name>'        Complete reminder\n", stderr)
    fputs("  contacts search '<name>'           Search contacts\n", stderr)
    fputs("  contacts reverse '<phone>'         Reverse phone lookup\n", stderr)
    exit(1)
}

let service = args[1]
let command = args[2]

switch (service, command) {
case ("calendar", "today"):
    calendarToday()
case ("calendar", "range"):
    guard args.count >= 4, let days = Int(args[3]) else { fail("Usage: pim-tool calendar range <days>") }
    calendarRange(days)
case ("calendar", "create"):
    guard args.count >= 4 else { fail("Usage: pim-tool calendar create '<json>'") }
    calendarCreate(args[3])
case ("calendar", "edit"):
    guard args.count >= 4 else { fail("Usage: pim-tool calendar edit '<json>'") }
    calendarEdit(args[3])
case ("calendar", "delete"):
    guard args.count >= 4 else { fail("Usage: pim-tool calendar delete '<summary>'") }
    calendarDelete(args[3])
case ("reminders", "list"):
    remindersList()
case ("reminders", "create"):
    guard args.count >= 4 else { fail("Usage: pim-tool reminders create '<json>'") }
    remindersCreate(args[3])
case ("reminders", "complete"):
    guard args.count >= 4 else { fail("Usage: pim-tool reminders complete '<name>'") }
    remindersComplete(args[3])
case ("contacts", "search"):
    guard args.count >= 4 else { fail("Usage: pim-tool contacts search '<name>'") }
    contactsSearch(args[3])
case ("contacts", "reverse"):
    guard args.count >= 4 else { fail("Usage: pim-tool contacts reverse '<phone>'") }
    contactsReverse(args[3])
default:
    fail("Unknown command: \(service) \(command)")
}
