let audio: AudioContext | null = null;

export function communicationSoundActive(): boolean {
  return audio?.state === "running";
}

export async function activateCommunicationSound(): Promise<void> {
  if (typeof AudioContext === "undefined") throw new Error("Sound is not supported by this browser.");
  audio ||= new AudioContext();
  await audio.resume();
  if (audio.state !== "running") throw new Error("Sound could not be enabled in this browser.");
}

export function playCommunicationSound(): void {
  if (!audio || audio.state !== "running") return;
  const tone = audio.createOscillator();
  const volume = audio.createGain();
  tone.frequency.value = 660;
  volume.gain.setValueAtTime(0.04, audio.currentTime);
  volume.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.16);
  tone.connect(volume); volume.connect(audio.destination);
  tone.start(); tone.stop(audio.currentTime + 0.16);
}
