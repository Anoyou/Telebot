// 设备伪装库 API 包装。
import { api } from "@/lib/api";
import type {
  DeviceProfileCreate,
  DeviceProfileOut,
  DeviceProfileUpdate,
} from "@/api/types";

export async function listDeviceProfiles(): Promise<DeviceProfileOut[]> {
  const { data } = await api.get<DeviceProfileOut[]>("/api/device-profiles");
  return data;
}

export async function createDeviceProfile(
  payload: DeviceProfileCreate,
): Promise<DeviceProfileOut> {
  const { data } = await api.post<DeviceProfileOut>(
    "/api/device-profiles",
    payload,
  );
  return data;
}

export async function patchDeviceProfile(
  pid: number,
  payload: DeviceProfileUpdate,
): Promise<DeviceProfileOut> {
  const { data } = await api.patch<DeviceProfileOut>(
    `/api/device-profiles/${pid}`,
    payload,
  );
  return data;
}

export async function setDefaultDeviceProfile(
  pid: number,
): Promise<DeviceProfileOut> {
  const { data } = await api.post<DeviceProfileOut>(
    `/api/device-profiles/${pid}/default`,
  );
  return data;
}

export async function deleteDeviceProfile(pid: number): Promise<void> {
  await api.delete(`/api/device-profiles/${pid}`);
}
