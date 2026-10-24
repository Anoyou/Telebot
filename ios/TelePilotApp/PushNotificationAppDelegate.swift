import UIKit

/// UIKit owns remote-notification registration; SwiftUI owns presentation and routing.
final class PushNotificationAppDelegate: NSObject, UIApplicationDelegate {
    static weak var manager: PushNotificationManager?

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        Self.manager?.registered(deviceToken: deviceToken)
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        Self.manager?.failedToRegister(error: error)
    }
}
