self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(async (clients) => {
      for (const client of clients) {
        if (typeof client.focus === "function") {
          await client.focus();
          client.postMessage({ type: "corral:open-cloudtalk-phone" });
          return;
        }
      }
      await self.clients.openWindow("/");
    }),
  );
});
