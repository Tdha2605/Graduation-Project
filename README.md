Đồ án tập trung vào việc xây dựng hệ thống quản lý vào ra cho các phòng trong
tòa nhà, sử dụng thiết bị nền tảng là Raspberry Pi 4 tích hợp camera, cảm biến và
khóa điện tử. Hệ thống hỗ trợ nhận diện khuôn mặt và xác thực vân tay nhằm tăng
cường bảo mật và thuận tiện trong việc kiểm soát truy cập.
Hệ thống được chia thành ba phần chính, phối hợp chặt chẽ với nhau để đảm bảo
quá trình đăng ký, nhận diện và quản lý truy cập diễn ra hiệu quả:
Thiết bị đăng ký sinh trắc học: Đây là thiết bị chuyên dùng cho việc thu thập dữ
liệu sinh trắc học từ người dùng, bao gồm khuôn mặt và dấu vân tay. Thiết bị được
tích hợp camera chất lượng cao và cảm biến vân tay AS608, cho phép chụp ảnh
chân dung và ghi nhận mẫu vân tay với độ chính xác cao. Sau khi thu thập, dữ liệu
sinh trắc được xử lý sơ bộ và gửi lên server để lưu trữ vào cơ sở dữ liệu nhận diện,
phục vụ cho các bước xác thực sau này.
Thiết bị nhận diện sinh trắc học: Đây là các thiết bị được lắp đặt tại các cửa ra
vào của tòa nhà, thực hiện nhiệm vụ xác thực người dùng. Thiết bị sử dụng camera
để nhận diện khuôn mặt và cảm biến vân tay để xác thực dấu vân tay. Nếu dữ liệu
sinh trắc khớp với thông tin trong cơ sở dữ liệu server, thiết bị sẽ điều khiển khóa
điện từ để mở cửa, đồng thời ghi lại sự kiện truy cập và gửi thông tin về server. Các
thiết bị cũng liên tục theo dõi trạng thái cửa, phát hiện hành vi bất thường như mở
cửa quá thời gian quy định hoặc tác động vật lý lên thiết bị.
Server trung tâm: Server đóng vai trò là bộ não của hệ thống, tiếp nhận và lưu trữ
toàn bộ dữ liệu sinh trắc học, sự kiện ra vào, cũng như trạng thái thiết bị. Server
vận hành giao tiếp với các thiết bị thông qua giao thức MQTT, đảm nhiệm việc
phân phối dữ liệu nhận diện, gửi lệnh điều khiển mở cửa hoặc cập nhật danh sách
người dùng mới. Ngoài ra, server còn đảm bảo tính toàn vẹn dữ liệu, hỗ trợ giám
sát thời gian thực và phân tích lịch sử truy cập khi cần thiết.
Đồ án không chỉ giúp sinh viên củng cố kiến thức về IoT, xử lý ảnh và giao tiếp
MQTT, mà còn rèn luyện kỹ năng thiết kế hệ thống thực tế, từ phần cứng đến phần
mềm, góp phần nâng cao khả năng tích hợp và phát triển các ứng dụng thông minh
trong thực tiễn
