/** Whether publishing is set up, and through which provider: the one rule
 *  the Clips page and the Editor page's Publish both follow, so the same
 *  one-clip publish window opens from either (FL3, review fix S1).
 *
 *  Publishing is offered once WoopSocial or Upload-Post is switched on AND
 *  has a key. WoopSocial is used when it is set up, else Upload-Post. */
import { api } from './api'
import type { Provider } from './uploadpost'

export interface PublishSetup {
  ready: boolean
  provider: Provider
}

export async function readPublishSetup(): Promise<PublishSetup> {
  const [woop, up] = await Promise.allSettled([api.woopSocialStatus(), api.uploadPostStatus()])
  const wsReady = woop.status === 'fulfilled' && Boolean(woop.value.enabled && woop.value.has_key)
  const upReady = up.status === 'fulfilled' && Boolean(up.value.enabled && up.value.has_key)
  return { ready: wsReady || upReady, provider: wsReady ? 'woopsocial' : 'uploadpost' }
}
