/** Whether publishing is set up, and through which provider: the one rule
 *  the Clips page and the Editor page's Publish both follow, so the same
 *  one-clip publish window opens from either (FL3, review fix S1).
 *
 *  A provider is ready once WoopSocial or Upload-Post is switched on AND has
 *  a key. WoopSocial is used when it is set up, else Upload-Post. `youtube`
 *  is direct upload to YouTube switched on in Settings: the one-clip window
 *  has a section for it (step KC, moved from Quick edit), so one clip can be
 *  published with only that on. Publish is always shown on both pages; with
 *  nothing set up, the window says how to connect an account. */
import { api } from './api'
import type { Provider } from './uploadpost'

export interface PublishSetup {
  /** WoopSocial or Upload-Post is ready. */
  ready: boolean
  provider: Provider
  /** Direct upload to YouTube is switched on. */
  youtube: boolean
}

export async function readPublishSetup(): Promise<PublishSetup> {
  const [woop, up, yt] = await Promise.allSettled([
    api.woopSocialStatus(),
    api.uploadPostStatus(),
    api.youtubeStatus()
  ])
  const wsReady = woop.status === 'fulfilled' && Boolean(woop.value.enabled && woop.value.has_key)
  const upReady = up.status === 'fulfilled' && Boolean(up.value.enabled && up.value.has_key)
  const youtube = yt.status === 'fulfilled' && Boolean(yt.value.enabled)
  return { ready: wsReady || upReady, provider: wsReady ? 'woopsocial' : 'uploadpost', youtube }
}
